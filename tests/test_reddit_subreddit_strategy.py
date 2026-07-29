"""Generic policy tests: filtering is post-discovery and never scopes search."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lib import normalize, reddit, reddit_policy, schema, signals


class RedditSubredditStrategyTests(unittest.TestCase):
    def setUp(self):
        self.original = (normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS, normalize.PREFERRED_SUBREDDITS)
        normalize.BLOCKED_SUBREDDITS = frozenset({"bannedtestforum"})
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        normalize.PREFERRED_SUBREDDITS = {"preferredtestforum"}

    def tearDown(self):
        normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS, normalize.PREFERRED_SUBREDDITS = self.original

    def _raw(self, subreddit: str) -> dict:
        return {
            "id": "R1", "title": "Workflow discussion", "url": "https://reddit.com/r/test/comments/1/",
            "date": "2026-07-10", "subreddit": subreddit, "engagement": {"score": 10, "num_comments": 2},
            "top_comments": [{"author": "reader", "excerpt": "usable", "score": 3}],
        }

    def test_configured_global_blocks_are_case_insensitive_and_accept_r_prefix(self):
        for value in ("BannedTestForum", "bannedtestforum", "r/BannedTestForum", "R/BANNEDTESTFORUM"):
            with self.subTest(value=value):
                self.assertEqual(normalize.normalize_source_items("reddit", [self._raw(value)], "2026-07-01", "2026-07-25"), [])

    def test_removing_a_block_restores_eligibility(self):
        normalize.BLOCKED_SUBREDDITS = frozenset()
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        self.assertEqual(len(normalize.normalize_source_items("reddit", [self._raw("BannedTestForum")], "2026-07-01", "2026-07-25")), 1)

    def test_unlisted_subreddit_remains_eligible_and_discovery_remains_open(self):
        self.assertEqual(len(normalize.normalize_source_items("reddit", [self._raw("UnlistedTestForum")], "2026-07-01", "2026-07-25")), 1)
        self.assertEqual(reddit.discover_subreddits([{"subreddit": "UnlistedTestForum", "title": "Workflow", "ups": 20}], topic="Workflow"), ["UnlistedTestForum"])

    def test_preference_is_secondary_to_unchanged_four_signal_formula(self):
        base = signals.reddit_rank_score(0.8, 0.7, 0.6, 0.5)
        self.assertAlmostEqual(base, 0.35 * 0.8 + 0.25 * 0.7 + 0.25 * 0.6 + 0.15 * 0.5)
        self.assertAlmostEqual(normalize.reddit_subreddit_quality_multiplier("PreferredTestForum"), 1.5)
        self.assertEqual(normalize.reddit_subreddit_quality_multiplier("UnlistedTestForum"), 1.0)

    def test_missing_preferences_configuration_has_no_hidden_default(self):
        policy = reddit_policy.load_policy()
        self.assertEqual(frozenset(), policy.preferred_subreddits)

    def test_duplicate_block_entries_are_rejected_by_policy_loader(self):
        with patch.object(reddit_policy, "_read_object", return_value={"version": 1, "blocked_subreddits": ["r/DuplicateTestForum", "duplicatetestforum"]}):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                reddit_policy.load_blocklist(__import__("pathlib").Path("policy.json"))


if __name__ == "__main__":
    unittest.main()
