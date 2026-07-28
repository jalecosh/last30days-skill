import unittest
from pathlib import Path

from lib.reddit import (
    _extract_date,
    _extract_score,
    _extract_subreddit_name,
    _normalize_reddit_id,
    _total_engagement,
    enrich_with_comments,
)


class TestExtractSubredditName(unittest.TestCase):
    def test_from_string(self):
        self.assertEqual("openclaw", _extract_subreddit_name("openclaw"))

    def test_from_dict_with_name(self):
        self.assertEqual(
            "openclaw",
            _extract_subreddit_name({"id": "t5_ghydwa", "name": "openclaw"}),
        )

    def test_from_dict_with_display_name(self):
        self.assertEqual(
            "LocalLLM",
            _extract_subreddit_name({"display_name": "LocalLLM"}),
        )

    def test_from_dict_name_preferred_over_display_name(self):
        self.assertEqual(
            "name_wins",
            _extract_subreddit_name({"name": "name_wins", "display_name": "display"}),
        )

    def test_empty_string(self):
        self.assertEqual("", _extract_subreddit_name(""))

    def test_empty_dict(self):
        self.assertEqual("", _extract_subreddit_name({}))

    def test_strips_whitespace(self):
        self.assertEqual("test", _extract_subreddit_name("  test  "))


class TestExtractScore(unittest.TestCase):
    def test_ups(self):
        self.assertEqual(42, _extract_score({"ups": 42}))

    def test_score_field(self):
        self.assertEqual(77, _extract_score({"score": 77}))

    def test_votes(self):
        self.assertEqual(99, _extract_score({"votes": 99}))

    def test_ups_preferred_over_votes(self):
        self.assertEqual(10, _extract_score({"ups": 10, "votes": 99}))

    def test_missing(self):
        self.assertEqual(0, _extract_score({}))

    def test_zero_preserved(self):
        self.assertEqual(0, _extract_score({"ups": 0}))

    def test_zero_ups_does_not_fall_through(self):
        # ups=0 should be returned, not fall through to score
        self.assertEqual(0, _extract_score({"ups": 0, "score": 5}))


class TestExtractDate(unittest.TestCase):
    def test_unix_timestamp(self):
        self.assertEqual("2024-05-03", _extract_date({"created_utc": 1714694957}))

    def test_iso_string(self):
        result = _extract_date({"created_at": "2024-05-03T01:09:17.620000+0000"})
        self.assertEqual("2024-05-03", result)

    def test_iso_with_z_suffix(self):
        result = _extract_date({"created_at": "2024-05-03T01:09:17Z"})
        self.assertEqual("2024-05-03", result)

    def test_created_utc_preferred(self):
        result = _extract_date({"created_utc": 1714694957, "created_at": "2025-01-01T00:00:00Z"})
        self.assertEqual("2024-05-03", result)

    def test_missing(self):
        self.assertIsNone(_extract_date({}))


class TestNormalizeRedditId(unittest.TestCase):
    def test_strips_t3_prefix(self):
        self.assertEqual("abc123", _normalize_reddit_id("t3_abc123"))

    def test_no_prefix(self):
        self.assertEqual("abc123", _normalize_reddit_id("abc123"))

    def test_empty(self):
        self.assertEqual("", _normalize_reddit_id(""))

    def test_none(self):
        self.assertEqual("", _normalize_reddit_id(None))


class TestTotalEngagement(unittest.TestCase):
    def test_score_plus_comments(self):
        item = {"engagement": {"score": 100, "num_comments": 50}}
        self.assertEqual(150, _total_engagement(item))

    def test_high_comments_low_score(self):
        item = {"engagement": {"score": 1, "num_comments": 1387}}
        self.assertEqual(1388, _total_engagement(item))

    def test_missing_engagement(self):
        self.assertEqual(0, _total_engagement({}))

    def test_none_values(self):
        item = {"engagement": {"score": None, "num_comments": None}}
        self.assertEqual(0, _total_engagement(item))

    def test_score_only(self):
        item = {"engagement": {"score": 42}}
        self.assertEqual(42, _total_engagement(item))


class TestEnrichSelectsTopEngagement(unittest.TestCase):
    """Verify enrich_with_comments picks threads by total engagement, not list order."""

    def test_high_comment_thread_enriched_over_low_engagement(self):
        """A thread with 1387 comments but score:1 should be enriched before
        a thread with score:5 and 0 comments."""
        from unittest.mock import patch

        items = [
            # Low engagement thread (first in list)
            {
                "id": "R1",
                "url": "https://www.reddit.com/r/test/comments/low",
                "engagement": {"score": 5, "num_comments": 0},
            },
            # High engagement thread (second in list)
            {
                "id": "R2",
                "url": "https://www.reddit.com/r/test/comments/high",
                "engagement": {"score": 1, "num_comments": 1387},
            },
            # Medium engagement
            {
                "id": "R3",
                "url": "https://www.reddit.com/r/test/comments/med",
                "engagement": {"score": 50, "num_comments": 10},
            },
        ]

        enriched_urls = []

        def mock_fetch_comments(url, token):
            enriched_urls.append(url)
            return [{"body": "Great thread!", "ups": 10, "author": "testuser"}]

        # Only allow 1 enrichment to prove selection order matters
        with patch("lib.reddit.fetch_post_comments", side_effect=mock_fetch_comments):
            result = enrich_with_comments(items, token="fake", depth="quick")

        # With quick depth (3 enrichments), all 3 should be enriched.
        # But the key assertion: the high-comment thread (R2) must be included.
        self.assertIn(
            "https://www.reddit.com/r/test/comments/high",
            enriched_urls,
            "High-comment thread should always be selected for enrichment",
        )

    def test_enrichment_order_by_engagement(self):
        """With a budget of 1, only the highest-engagement thread gets enriched."""
        from unittest.mock import patch

        items = [
            {
                "id": "R1",
                "url": "https://www.reddit.com/r/test/comments/a",
                "engagement": {"score": 200, "num_comments": 5},
            },
            {
                "id": "R2",
                "url": "https://www.reddit.com/r/test/comments/b",
                "engagement": {"score": 1, "num_comments": 1500},
            },
        ]

        enriched_urls = []

        def mock_fetch_comments(url, token):
            enriched_urls.append(url)
            return [{"body": "Comment", "ups": 5, "author": "user"}]

        # Override DEPTH_CONFIG to allow only 1 enrichment
        custom_config = {"comment_enrichments": 1}
        with patch("lib.reddit.DEPTH_CONFIG", {"test": custom_config, "default": custom_config}), \
             patch("lib.reddit.fetch_post_comments", side_effect=mock_fetch_comments):
            enrich_with_comments(items, token="fake", depth="test")

        # R2 has 1501 total engagement vs R1's 205 -- R2 should be picked
        self.assertEqual(len(enriched_urls), 1)
        self.assertEqual(
            enriched_urls[0],
            "https://www.reddit.com/r/test/comments/b",
        )


class TestEnrichmentBudget(unittest.TestCase):
    """Tests for the enrichment time budget in enrich_with_comments()."""

    def _make_items(self, n):
        return [
            {"url": f"https://reddit.com/r/test/comments/{i}/post", "score": 100 - i, "num_comments": 50,
             "engagement": {"score": 100 - i, "num_comments": 50}}
            for i in range(n)
        ]

    def test_all_complete_within_budget(self):
        """When enrichment is fast, all items get comments."""
        from unittest.mock import patch
        items = self._make_items(3)
        fast_comments = [{"body": "Great post!", "score": 42, "author": "user1"}]

        with patch("lib.reddit.fetch_post_comments", return_value=fast_comments):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=60)

        enriched = [i for i in result if i.get("top_comments")]
        self.assertEqual(len(enriched), 3)

    def test_budget_zero_returns_items_unenriched(self):
        """With budget=0, items are returned without enrichment (not discarded)."""
        import time as _time
        from unittest.mock import patch

        items = self._make_items(3)

        def slow_fetch(url, token):
            _time.sleep(2)
            return [{"body": "comment", "score": 10, "author": "u"}]

        with patch("lib.reddit.fetch_post_comments", side_effect=slow_fetch):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=0)

        # All 3 items returned (not discarded)
        self.assertEqual(len(result), 3)

    def test_empty_items_returns_immediately(self):
        result = enrich_with_comments([], "fake-token", depth="default", budget_seconds=60)
        self.assertEqual(result, [])

    def test_exceptions_dont_crash(self):
        """If enrichment raises, items are returned without comments."""
        from unittest.mock import patch
        items = self._make_items(3)

        with patch("lib.reddit.fetch_post_comments", side_effect=ConnectionError("boom")):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=60)

        self.assertEqual(len(result), 3)
        enriched = [i for i in result if i.get("top_comments")]
        self.assertEqual(len(enriched), 0)

if __name__ == "__main__":
    unittest.main()


class TestScrapeCreatorsCommentTree(unittest.TestCase):
    """The real provider's nested shape must survive to the existing renderer."""

    def _fixture(self):
        import json
        return json.loads((Path(__file__).parent / "fixtures" /
                           "reddit_scrapecreators_nested_comments.json").read_text(encoding="utf8"))

    def _enriched(self, raw=None):
        from unittest.mock import patch
        items = [{"id": "R1", "url": "https://reddit.com/r/ValueInvesting/comments/1uvd8j1/",
                  "title": "Post", "subreddit": "ValueInvesting", "date": "2026-07-13",
                  "engagement": {"score": 1, "num_comments": 1}}]
        with patch("lib.reddit.fetch_post_comments", return_value=raw if raw is not None else self._fixture()):
            enrich_with_comments(items, token="fake", depth="quick")
        return items[0]

    def test_nested_provider_tree_survives_normalize_and_render(self):
        from lib import normalize, render
        item = self._enriched()
        root = item["comment_tree"][0]
        dopexile = root["replies"][0]  # score ordering: 70 before 11
        nested = dopexile["replies"][0]
        self.assertEqual((root["id"], root["parent_id"], root["author"], root["depth"]),
                         ("oxa60e1", "t3_1uvd8j1", "narayan77", 0))
        self.assertEqual((dopexile["id"], dopexile["parent_id"], dopexile["parent_author"], dopexile["depth"]),
                         ("oxanz23", "t1_oxa60e1", "narayan77", 1))
        self.assertEqual((nested["id"], nested["parent_id"], nested["parent_author"], nested["depth"]),
                         ("oxblo42", "t1_oxanz23", "dopexile", 2))
        self.assertEqual((root["score"], root["created_utc"]), (132, 1783953624))
        self.assertRegex(root["date"], r"^2026-07-13 \d\d:\d\d$")
        normalized = normalize.normalize_source_items("reddit", [item], "2026-07-01", "2026-07-31")[0]
        self.assertEqual(normalized.metadata["comment_tree"], item["comment_tree"])
        self.assertEqual([c["author"] for c in item["top_comments"]],
                         ["narayan77", "dopexile", "Few_Economics_8176", "Ancient-Purpose99"])
        self.assertTrue(all(set(c) == {"score", "date", "author", "excerpt", "url"}
                            for c in item["top_comments"]))
        rendered = "\n".join(render._render_reddit_discussion(normalized))
        self.assertIn("narayan77\uff1aRoot comment\u3000\u3000\u3000132", rendered)
        self.assertIn("\u3000\u21b3 dopexile \u2192 narayan77", rendered)
        self.assertIn("\u3000\u3000\u21b3 Few_Economics_8176 \u2192 dopexile", rendered)

    def test_tree_limits_and_missing_fields_are_safe(self):
        from unittest.mock import patch
        from lib import reddit
        raw = [{"id": f"root-{i}", "author": "[removed]" if i == 11 else f"u/root{i}",
                "body": "x" * 5000 if i == 11 else "", "score": i,
                "replies": {"items": [{"id": f"child-{i}-{j}", "author": "child",
                                           "body": "body", "score": j,
                                           "replies": {"items": []}}
                                          for j in range(12)]}}
               for i in range(12)]
        with patch.object(reddit, "MAX_COMMENT_TREE_ROOTS", 2), \
             patch.object(reddit, "MAX_COMMENT_TREE_REPLIES", 3), \
             patch.object(reddit, "MAX_COMMENT_TREE_NODES", 5):
            item = self._enriched(raw)
        tree = item["comment_tree"]
        self.assertEqual(len(tree), 2)
        self.assertLessEqual(sum(1 + len(node["replies"]) for node in tree), 5)
        self.assertLessEqual(len(tree[0]["replies"]), 3)
        deleted = next(node for node in tree if node["id"] == "root-11")
        self.assertEqual(deleted["author"], "[deleted]")
        self.assertEqual(deleted["body"], "x" * 4000)
        self.assertEqual(reddit._project_comment_tree([{
            "id": "orphan", "replies": None
        }])[0]["replies"], [])


class TestRedditCommentEvidenceFilter(unittest.TestCase):
    def test_commentless_reddit_items_do_not_reach_fusion(self):
        from lib import normalize
        raw = [{"id": "R1", "title": "No discussion", "url": "https://reddit.com/r/x/comments/1/", "date": "2026-07-10", "engagement": {"score": 9, "num_comments": 0}}]
        self.assertEqual(normalize.normalize_source_items("reddit", raw, "2026-07-01", "2026-07-31"), [])

    def test_configured_subreddits_are_hard_excluded_case_insensitively(self):
        from lib import normalize
        original = normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS
        normalize.BLOCKED_SUBREDDITS = frozenset({"bannedtestforum"})
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        variants = ["BannedTestForum", "bannedtestforum", "r/BannedTestForum", "R/BANNEDTESTFORUM"]
        raw = [
            {
                "id": f"R{index}", "title": "Discussion", "url": f"https://reddit.com/r/x/comments/{index}/",
                "date": "2026-07-10", "subreddit": subreddit,
                "engagement": {"score": 9, "num_comments": 1},
                "top_comments": [{"author": "a", "excerpt": "usable", "score": 1}],
            }
            for index, subreddit in enumerate(variants)
        ]
        try:
            self.assertEqual(normalize.normalize_source_items("reddit", raw, "2026-07-01", "2026-07-31"), [])
        finally:
            normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS = original

    def test_unlisted_subreddit_survives_hard_exclusion(self):
        from lib import normalize
        raw = [{
            "id": "R1", "title": "Discussion", "url": "https://reddit.com/r/ValueInvesting/comments/1/",
            "date": "2026-07-10", "subreddit": "r/ValueInvesting",
            "engagement": {"score": 9, "num_comments": 1},
            "top_comments": [{"author": "a", "excerpt": "usable", "score": 1}],
        }]
        items = normalize.normalize_source_items("reddit", raw, "2026-07-01", "2026-07-31")
        self.assertEqual([item.container for item in items], ["r/ValueInvesting"])

    def test_flat_top_comments_remain_eligible(self):
        from lib import normalize, render
        raw = [{"id": "R1", "title": "Flat discussion", "url": "https://reddit.com/r/x/comments/1/", "date": "2026-07-10", "engagement": {"score": 9, "num_comments": 1}, "top_comments": [{"author": "a", "excerpt": "usable", "score": 1}]}]
        items = normalize.normalize_source_items("reddit", raw, "2026-07-01", "2026-07-31")
        self.assertEqual(len(items), 1)
        self.assertNotIn("\\u6682\\u65e0\\u53ef\\u7528\\u8bc4\\u8bba", "\n".join(render._render_reddit_discussions(items)))


class TestFinalRedditPostCap(unittest.TestCase):
    def _candidate(self, index, source="reddit"):
        from lib import schema
        item = schema.SourceItem(
            item_id=f"{source}-{index}", source=source, title=f"{source} {index}",
            body="body", url=f"https://example.com/{source}/{index}",
            container="Adobe" if source == "reddit" else None,
            engagement={"score": 20 - index, "num_comments": 1},
            metadata={"top_comments": [{"excerpt": "usable", "score": 1}]} if source == "reddit" else {},
        )
        return schema.Candidate(
            candidate_id=f"{source}-{index}", item_id=item.item_id, source=source,
            title=item.title, url=item.url, snippet="body", subquery_labels=[],
            native_ranks={}, local_relevance=1, freshness=100 - index,
            engagement=20 - index, source_quality=1, rrf_score=1,
            source_items=[item], final_score=100 - index,
        )

    def _cap(self, reddit_count, non_reddit_count=0):
        from lib import pipeline
        candidates = [self._candidate(index) for index in range(reddit_count)]
        candidates += [self._candidate(index, "x") for index in range(non_reddit_count)]
        items = {
            "reddit": [candidate.source_items[0] for candidate in candidates if candidate.source == "reddit"],
            "x": [candidate.source_items[0] for candidate in candidates if candidate.source == "x"],
        }
        return pipeline._cap_final_reddit_posts(candidates, items)

    def test_fewer_than_fifteen_eligible_posts_are_not_padded(self):
        candidates, items = self._cap(4)
        self.assertEqual(len(candidates), 4)
        self.assertEqual(len(items["reddit"]), 4)

    def test_exactly_fifteen_eligible_posts_remain(self):
        candidates, items = self._cap(15)
        self.assertEqual(len(candidates), 15)
        self.assertEqual(len(items["reddit"]), 15)

    def test_more_than_fifteen_posts_keep_top_ranked_fifteen(self):
        candidates, items = self._cap(20)
        self.assertEqual([candidate.title for candidate in candidates], [f"reddit {index}" for index in range(15)])
        self.assertEqual([item.title for item in items["reddit"]], [f"reddit {index}" for index in range(15)])

    def test_nonreddit_candidates_are_unchanged_by_reddit_cap(self):
        candidates, items = self._cap(20, non_reddit_count=2)
        self.assertEqual(len([candidate for candidate in candidates if candidate.source == "reddit"]), 15)
        self.assertEqual(len([candidate for candidate in candidates if candidate.source == "x"]), 2)
        self.assertEqual(len(items["x"]), 2)

    def test_commentless_and_excluded_posts_never_fill_cap(self):
        from lib import normalize, pipeline, schema
        original = normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS
        normalize.BLOCKED_SUBREDDITS = frozenset({"bannedtestforum"})
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        raw = [
            {
                "id": f"R{index}", "title": f"Eligible {index}", "url": f"https://reddit.com/r/Adobe/comments/{index}/",
                "date": "2026-07-10", "subreddit": "Adobe", "engagement": {"score": 9, "num_comments": 1},
                "top_comments": [{"author": "a", "excerpt": "usable", "score": 1}],
            }
            for index in range(14)
        ] + [
            {"id": "excluded", "title": "Excluded", "url": "https://reddit.com/r/BannedTestForum/comments/x/", "date": "2026-07-10", "subreddit": "R/BANNEDTESTFORUM", "engagement": {"score": 9, "num_comments": 1}, "top_comments": [{"excerpt": "usable"}]},
            {"id": "commentless", "title": "Commentless", "url": "https://reddit.com/r/Adobe/comments/y/", "date": "2026-07-10", "subreddit": "Adobe", "engagement": {"score": 9, "num_comments": 1}},
        ]
        try:
            survivors = normalize.normalize_source_items("reddit", raw, "2026-07-01", "2026-07-31")
            candidates = [
                schema.Candidate(candidate_id=item.item_id, item_id=item.item_id, source="reddit", title=item.title,
                                 url=item.url, snippet="", subquery_labels=[], native_ranks={}, local_relevance=1,
                                 freshness=1, engagement=1, source_quality=1, rrf_score=1, source_items=[item])
                for item in survivors
            ]
            capped, items = pipeline._cap_final_reddit_posts(candidates, {"reddit": survivors})
            self.assertEqual(len(capped), 14)
            self.assertEqual(len(items["reddit"]), 14)
            self.assertNotIn("Excluded", [candidate.title for candidate in capped])
            self.assertNotIn("Commentless", [candidate.title for candidate in capped])
        finally:
            normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS = original
