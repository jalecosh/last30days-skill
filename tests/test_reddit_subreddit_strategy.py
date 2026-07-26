"""Focused policy tests for open Reddit discovery and light subreddit quality weights."""

from __future__ import annotations

import unittest

from lib import normalize, reddit, rerank, schema, signals


class RedditSubredditStrategyTests(unittest.TestCase):
    def _raw_reddit(self, subreddit: str, *, score: int = 10, comments: int = 2) -> dict:
        return {
            "id": subreddit.replace("/", "-").lower() or "post",
            "title": "Adobe workflow discussion",
            "url": f"https://reddit.com/r/{subreddit.removeprefix('r/')}/comments/1/post",
            "date": "2026-07-10",
            "subreddit": subreddit,
            "engagement": {"score": score, "num_comments": comments},
            "top_comments": [{"author": "reader", "excerpt": "usable discussion", "score": 3}],
        }

    def _item(self, subreddit: str, *, score: int, comments: int) -> schema.SourceItem:
        return schema.SourceItem(
            item_id=subreddit.replace("/", "-").lower(),
            source="reddit",
            title="Adobe workflow discussion",
            body="Adobe workflow discussion with practical details.",
            url=f"https://reddit.com/r/{subreddit.removeprefix('r/')}/comments/1/post",
            container=subreddit,
            published_at="2026-07-24",
            engagement={"score": score, "num_comments": comments},
            metadata={"comment_tree": [{"author": "reader", "body": "usable", "score": 1}]},
        )

    def test_discovery_remains_open_to_unknown_subreddits(self):
        discovered = reddit.discover_subreddits(
            [{"subreddit": "creativeworkflow", "title": "Adobe workflow", "ups": 200}],
            topic="Adobe workflow",
        )
        self.assertEqual(discovered, ["creativeworkflow"])
        self.assertNotIn("creativeworkflow", normalize.SUBREDDIT_QUALITY_MULTIPLIERS)

    def test_blocklist_normalizes_case_and_optional_prefix(self):
        for value in ("FuckAdobe", "fuckadobe", "r/FuckAdobe", "R/FUCKADOBE"):
            with self.subTest(value=value):
                self.assertEqual(normalize.reddit_subreddit_quality_multiplier(value), 0.0)
                self.assertEqual(
                    normalize.normalize_source_items(
                        "reddit", [self._raw_reddit(value)], "2026-07-01", "2026-07-25"
                    ),
                    [],
                )

    def test_preferred_and_unknown_subreddit_multipliers(self):
        cases = {
            "r/ValueInvesting": 1.08,
            "ADOBE": 1.00,
            "graphic_design": 1.00,
            "r/creativeworkflow": 1.00,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize.reddit_subreddit_quality_multiplier(value), expected)

    def test_global_preferred_map_has_no_company_specific_subreddits(self):
        self.assertEqual(normalize.SUBREDDIT_QUALITY_MULTIPLIERS, {"valueinvesting": 1.08})
        self.assertNotIn("adobe", normalize.SUBREDDIT_QUALITY_MULTIPLIERS)
        self.assertNotIn("graphic_design", normalize.SUBREDDIT_QUALITY_MULTIPLIERS)

    def test_unknown_subreddit_remains_eligible(self):
        items = normalize.normalize_source_items(
            "reddit", [self._raw_reddit("r/creativeworkflow")], "2026-07-01", "2026-07-25"
        )
        ranked = signals.annotate_stream(
            items, "Adobe workflow", "balanced_recent", reference_date="2026-07-25"
        )
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].container, "r/creativeworkflow")
        self.assertEqual(ranked[0].metadata["reddit_subreddit_quality_multiplier"], 1.00)

    def test_strong_nonpreferred_subreddit_can_outrank_weak_valueinvesting(self):
        strong_adobe = self._item("r/Adobe", score=800, comments=500)
        weak_preferred = self._item("r/ValueInvesting", score=1, comments=1)
        ranked = signals.annotate_stream(
            [weak_preferred, strong_adobe],
            "Adobe workflow",
            "balanced_recent",
            reference_date="2026-07-25",
        )
        self.assertEqual(ranked[0].container, "r/Adobe")
        self.assertGreater(ranked[0].local_rank_score, ranked[1].local_rank_score)

    def test_multiplier_applies_after_unchanged_four_signal_formula(self):
        base = signals.reddit_rank_score(0.8, 0.7, 0.6, 0.5)
        self.assertAlmostEqual(base, 0.35 * 0.8 + 0.25 * 0.7 + 0.25 * 0.6 + 0.15 * 0.5)
        self.assertAlmostEqual(
            base * normalize.reddit_subreddit_quality_multiplier("ValueInvesting"),
            base * 1.08,
        )

    def test_final_reddit_score_uses_the_same_post_base_multiplier(self):
        def candidate_for(subreddit: str) -> schema.Candidate:
            item = self._item(subreddit, score=50, comments=20)
            item.metadata.update({
                "reddit_normalized_comments": 0.4,
                "reddit_normalized_post_score": 0.3,
            })
            return schema.Candidate(
                candidate_id=subreddit, item_id=item.item_id, source="reddit",
                title=item.title, url=item.url, snippet=item.body, subquery_labels=[],
                native_ranks={}, local_relevance=0.8, freshness=80, engagement=20,
                source_quality=0.6, rrf_score=0.01, source_items=[item], rerank_score=70,
            )

        unknown = candidate_for("creativeworkflow")
        preferred = candidate_for("ValueInvesting")
        expected_base = 100.0 * signals.reddit_rank_score(0.7, 0.8, 0.4, 0.3)
        self.assertAlmostEqual(rerank._final_score(unknown), expected_base)
        self.assertAlmostEqual(rerank._final_score(preferred), expected_base * 1.08)

    def test_blocked_posts_are_not_restored_and_nonreddit_is_unchanged(self):
        blocked = self._item("r/FuckAdobe", score=20, comments=8)
        self.assertEqual(
            signals.annotate_stream([blocked], "Adobe", "balanced_recent", reference_date="2026-07-25"),
            [],
        )
        non_reddit = schema.SourceItem(
            item_id="x-1", source="x", title="Adobe workflow", body="Adobe workflow",
            url="https://x.example/1", published_at="2026-07-24", engagement={"likes": 10},
        )
        ranked = signals.annotate_stream(
            [non_reddit], "Adobe workflow", "balanced_recent", reference_date="2026-07-25"
        )
        self.assertEqual(ranked, [non_reddit])
        self.assertIsNotNone(non_reddit.local_rank_score)


if __name__ == "__main__":
    unittest.main()
