"""Focused final-score tests for generic Reddit investment preferences."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lib import normalize, reddit_policy, rerank, schema


PREFERENCES_PATH = Path(__file__).parents[1] / "config" / "reddit" / "preferences.json"


def _candidate(subreddit: str) -> schema.Candidate:
    item = schema.SourceItem(
        item_id=subreddit,
        source="reddit",
        title="Company investment discussion",
        body="Company investment discussion",
        url=f"https://reddit.com/r/{subreddit}/comments/post/",
        container=subreddit,
        published_at="2026-07-20",
        metadata={"reddit_normalized_comments": 0.0, "reddit_normalized_post_score": 0.0},
    )
    return schema.Candidate(
        candidate_id=subreddit,
        item_id=item.item_id,
        source="reddit",
        title=item.title,
        url=item.url,
        snippet=item.body,
        subquery_labels=[],
        native_ranks={},
        local_relevance=0.0,
        freshness=0.0,
        engagement=0.0,
        source_quality=0.0,
        rrf_score=0.0,
        source_items=[item],
        rerank_score=0.0,
    )


def test_generic_finance_preference_multiplies_only_completed_reddit_score() -> None:
    finance = _candidate("Finance")
    normal = _candidate("Autodesk")
    _, preferences = reddit_policy.load_preferences(PREFERENCES_PATH)
    assert preferences == {
        "finance": 1.5, "stocks": 1.5, "investing": 1.5,
        "securityanalysis": 1.5, "valueinvesting": 1.5,
    }
    with patch.object(normalize, "SUBREDDIT_QUALITY_MULTIPLIERS", preferences), patch.object(
        rerank.signals, "reddit_rank_score", side_effect=[0.50, 0.90]
    ):
        assert rerank._final_score(finance) == 75.0
        assert rerank._final_score(normal) == 90.0


def test_unknown_subreddit_keeps_multiplier_one_and_can_outrank_finance() -> None:
    finance = _candidate("investing")
    unknown = _candidate("product_support")
    with patch.object(normalize, "SUBREDDIT_QUALITY_MULTIPLIERS", {"investing": 1.5}), patch.object(
        rerank.signals, "reddit_rank_score", side_effect=[0.50, 0.90]
    ):
        finance_score = rerank._final_score(finance)
        unknown_score = rerank._final_score(unknown)
    assert finance_score == 75.0
    assert unknown_score == 90.0
    assert unknown_score > finance_score
