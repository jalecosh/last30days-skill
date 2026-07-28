import unittest
from unittest.mock import patch

from lib import pipeline, render, schema


LABELS = ("creative", "firefly", "subscription", "alternatives")


def _reddit_plan(*, sources=None):
    sources = sources or ["reddit"]
    return {
        "intent": "opinion",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "debate",
        "subqueries": [
            {
                "label": label,
                "search_query": f"Adobe {label}",
                "ranking_query": f"Adobe {label}",
                "sources": list(sources),
                "weight": 1.0,
            }
            for label in LABELS
        ],
        "source_weights": {source: 1.0 for source in sources},
    }


def _reddit_item(label, index, *, url=None, subreddit="creativeworkflow", comments=True):
    variant = (
        "licensing migration contract renewal cancellation customer retention"
        if index == 0 else
        "rendering performance color workflow templates collaboration training"
    )
    return {
        "id": f"{label}-{index}",
        "title": f"Adobe {label} {variant}",
        "selftext": f"{label} {variant}",
        "url": url or f"https://reddit.com/r/{subreddit}/comments/{label}{index}/post/",
        "date": "2026-07-20",
        "subreddit": subreddit,
        "relevance": 0.95,
        "engagement": {"score": 20, "num_comments": 4},
        "top_comments": [{"author": "commenter", "excerpt": "usable comment", "score": 2}] if comments else [],
        "_comment_enrichment_attempted": True,
    }


class RedditRecallPipelineTests(unittest.TestCase):
    def _run_reddit_plan(self, plan):
        submitted = []

        def retrieve(**kwargs):
            source = kwargs["source"]
            subquery = kwargs["subquery"]
            submitted.append((source, subquery.search_query))
            if source == "reddit":
                duplicate = "https://reddit.com/r/creativeworkflow/comments/shared/post/" if subquery.label in {"creative", "firefly"} else None
                return [
                    _reddit_item(subquery.label, 0, url=duplicate),
                    _reddit_item(subquery.label, 1),
                ], {}
            return [{
                "id": "job-1", "title": "Adobe engineer", "description": "role",
                "url": "https://jobs.example/1", "date": "2026-07-20",
            }], {}

        with patch("lib.pipeline._retrieve_stream", side_effect=retrieve), \
             patch("lib.pipeline._retry_thin_sources"), \
             patch("lib.pipeline._run_supplemental_searches"), \
             patch("lib.pipeline._load_library_context", return_value=(None, None)):
            report = pipeline.run(
                topic="Adobe", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
                depth="default", requested_sources=["reddit"], mock=True,
                external_plan=plan,
            )
        return report, submitted

    def test_reddit_only_plan_keeps_jobs_out_and_preserves_each_plan_query(self):
        report, submitted = self._run_reddit_plan(_reddit_plan())
        self.assertEqual({"reddit"}, {source for source, _query in submitted})
        self.assertEqual(
            {f"Adobe {label}" for label in LABELS},
            {query for _source, query in submitted},
        )
        self.assertNotIn("jobs", report.items_by_source)
        self.assertNotIn("jobs", report.source_status)

    def test_multi_angle_dedup_and_recall_counters_are_internal_and_exact(self):
        report, _submitted = self._run_reddit_plan(_reddit_plan())
        counters = report.artifacts["reddit_recall"]
        self.assertEqual({label: 2 for label in LABELS}, counters["raw_reddit_records_per_subquery"])
        self.assertEqual(8, counters["posts_submitted_for_comment_enrichment"])
        self.assertEqual(8, counters["posts_with_usable_comments"])
        self.assertEqual(7, counters["unique_posts_after_deduplication"])
        self.assertEqual(8, counters["enriched_candidates_entering_fusion"])
        self.assertEqual(7, counters["final_ranked_reddit_posts_before_cap"])
        self.assertEqual(7, counters["final_rendered_post_count"])
        self.assertNotIn("reddit_recall", render.render_full(report))

    def test_raw_gate_counters_keep_blocked_and_commentless_posts_out(self):
        from lib import normalize
        original = normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS
        normalize.BLOCKED_SUBREDDITS = frozenset({"bannedtestforum"})
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        try:
            stats = pipeline._reddit_raw_stats([
                _reddit_item("blocked", 1, subreddit="r/BannedTestForum"),
                _reddit_item("commentless", 2, comments=False),
                _reddit_item("usable", 3),
            ])
            self.assertEqual(1, stats["posts_removed_by_blocklist"])
            self.assertEqual(1, stats["posts_removed_as_commentless"])
            self.assertEqual(1, stats["posts_with_usable_comments"])
        finally:
            normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS = original

    def test_reddit_only_caps_raise_defaults_without_overriding_explicit_cli_limits(self):
        base = pipeline._resolve_depth_settings("default", {})
        raised = pipeline._reddit_recall_settings(base, {}, reddit_only=True)
        self.assertEqual(20, raised["per_stream_limit"])
        self.assertEqual(60, raised["pool_limit"])
        self.assertEqual(60, raised["rerank_limit"])
        explicit = pipeline._reddit_recall_settings(
            pipeline._resolve_depth_settings("default", {"_max_per_source": 9, "_max_results": 11}),
            {"_max_per_source": 9, "_max_results": 11}, reddit_only=True,
        )
        self.assertEqual({"per_stream_limit": 9, "pool_limit": 11, "rerank_limit": 11}, explicit)
        self.assertEqual(base, pipeline._reddit_recall_settings(base, {}, reddit_only=False))

    def test_actual_multi_source_plan_still_executes_jobs(self):
        submitted = []

        def retrieve(**kwargs):
            submitted.append(kwargs["source"])
            if kwargs["source"] == "reddit":
                return [_reddit_item(kwargs["subquery"].label, 1)], {}
            return [{
                "id": "job-1", "title": "Adobe engineer", "description": "role",
                "url": "https://jobs.example/1", "date": "2026-07-20",
            }], {}

        with patch("lib.pipeline._retrieve_stream", side_effect=retrieve), \
             patch("lib.pipeline._retry_thin_sources"), \
             patch("lib.pipeline._run_supplemental_searches"), \
             patch("lib.pipeline._load_library_context", return_value=(None, None)):
            report = pipeline.run(
                topic="Adobe", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
                depth="quick", requested_sources=["reddit", "jobs"], mock=True,
                hiring_signals_mode=True,
                external_plan=_reddit_plan(sources=["reddit", "jobs"]),
            )
        self.assertIn("jobs", submitted)
        self.assertIn("jobs", report.items_by_source)


if __name__ == "__main__":
    unittest.main()
