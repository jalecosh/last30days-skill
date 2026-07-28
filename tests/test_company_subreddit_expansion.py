"""Focused company-plan guard tests for subreddit expansion."""

from __future__ import annotations

from unittest.mock import patch

from lib import pipeline


def _plan(*, entity_terms: list[str]) -> dict:
    return {
        "intent": "opinion",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "reddit_entity_terms": entity_terms,
        "reddit_comment_enrichment_mode": "deferred_company_wide",
        "reddit_search_execution_mode": "group_scoped_subreddit_expansion",
        "reddit_comment_tree_budget": 2,
        "subqueries": [{
            "label": "investment-1", "group_id": "investment_and_valuation",
            "search_query": "Autodesk valuation", "ranking_query": "Autodesk valuation",
            "sources": ["reddit"],
        }],
        "source_weights": {"reddit": 1.0},
    }


def _post(title: str) -> dict:
    return {
        "id": title, "title": title, "selftext": "discussion",
        "url": f"https://reddit.com/r/test/comments/{title}/post/", "date": "2026-07-20",
        "subreddit": "test", "relevance": 0.9,
        "engagement": {"score": 10, "num_comments": 2},
        "top_comments": [{"author": "reader", "excerpt": "usable", "score": 1}],
    }


def _run(plan: dict, raw: list[dict]):
    def enrich(items, **_kwargs):
        return items, {"posts_skipped_by_comment_tree_budget": 0}

    with patch("lib.pipeline._retrieve_stream", return_value=(raw, {})), \
         patch("lib.pipeline.enrich_provisional_reddit_items", side_effect=enrich), \
         patch("lib.pipeline._retry_thin_sources"), \
         patch("lib.pipeline._run_supplemental_searches"), \
         patch("lib.pipeline._load_library_context", return_value=(None, None)):
        return pipeline.run(
            topic="Autodesk", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="default", requested_sources=["reddit"], mock=True, external_plan=plan,
        )


def test_company_plan_skips_expansion_and_keeps_title_eligible_base_candidate() -> None:
    with patch("lib.pipeline.expand_group_reddit_subreddits") as expand:
        report = _run(_plan(entity_terms=["Autodesk"]), [_post("Autodesk valuation"), _post("Technical support")])
    expand.assert_not_called()
    assert [item.title for item in report.items_by_source["reddit"]] == ["Autodesk valuation"]


def test_non_company_group_plan_still_expands() -> None:
    with patch("lib.pipeline.expand_group_reddit_subreddits", return_value=([], {})) as expand:
        _run(_plan(entity_terms=[]), [_post("Autodesk valuation")])
    expand.assert_called_once()
