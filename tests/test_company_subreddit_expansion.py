"""Focused company-plan guard tests for subreddit expansion."""

from __future__ import annotations

from unittest.mock import patch

from lib import normalize, pipeline, render, rerank, schema


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


def _post(title: str, *, subreddit: str = "test") -> dict:
    return {
        "id": title, "title": title, "selftext": "discussion",
        "url": f"https://reddit.com/r/test/comments/{title}/post/", "date": "2026-07-20",
        "subreddit": subreddit, "relevance": 0.9,
        "engagement": {"score": 10, "num_comments": 2},
        "top_comments": [{"author": "reader", "excerpt": "usable", "score": 1}],
    }


def _run(plan: dict, raw: list[dict], *, topic: str = "Autodesk"):
    def enrich(items, **_kwargs):
        return items, {"posts_skipped_by_comment_tree_budget": 0}

    with patch("lib.pipeline._retrieve_stream", return_value=(raw, {})), \
         patch("lib.pipeline.enrich_provisional_reddit_items", side_effect=enrich), \
         patch("lib.pipeline._retry_thin_sources"), \
         patch("lib.pipeline._run_supplemental_searches"), \
         patch("lib.pipeline._load_library_context", return_value=(None, None)):
        return pipeline.run(
            topic=topic, config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="default", requested_sources=["reddit"], mock=True, external_plan=plan,
        )


def test_company_plan_skips_expansion_and_keeps_title_eligible_base_candidate() -> None:
    with patch("lib.pipeline.expand_group_reddit_subreddits") as expand:
        report = _run(_plan(entity_terms=["Autodesk"]), [_post("Autodesk valuation"), _post("Technical support")])
    expand.assert_not_called()
    assert [item.title for item in report.items_by_source["reddit"]] == ["Autodesk valuation"]


def test_non_company_group_plan_still_expands_without_company_title_gate() -> None:
    with patch("lib.pipeline.expand_group_reddit_subreddits", return_value=([], {})) as expand:
        report = _run(_plan(entity_terms=[]), [_post("PTC earnings discussion")])
    expand.assert_called_once()
    assert [item.title for item in report.items_by_source["reddit"]] == ["PTC earnings discussion"]



def test_finance_ticker_title_enters_company_candidate_pool_without_expansion() -> None:
    plan = _plan(entity_terms=pipeline.company_title_eligibility_entities(
        ["PTC", "PTC Inc.", "Windchill"], "PTC",
    ))
    plan["subqueries"][0]["search_query"] = "PTC valuation"
    plan["subqueries"][0]["ranking_query"] = "PTC valuation"
    with patch.object(normalize, "PREFERRED_SUBREDDITS", {"valueinvesting"}), \
         patch("lib.pipeline.expand_group_reddit_subreddits") as expand:
        report = _run(plan, [_post("PTC earnings discussion", subreddit="ValueInvesting")], topic="PTC")
    expand.assert_not_called()
    assert [item.title for item in report.items_by_source["reddit"]] == ["PTC earnings discussion"]


def test_preferred_subreddit_ticker_lane_uses_native_backend_then_public_fallback() -> None:
    targeted_query = "PTC"
    subquery = schema.SubQuery(
        label="preferred-subreddit-stocks", group_id="preferred_subreddit_ticker",
        search_query=targeted_query, ranking_query=targeted_query, sources=["reddit"],
        reddit_target_subreddits=["stocks"],
    )
    public_post = _post("PTC earnings discussion", subreddit="r/StOcKs")
    outside_post = _post("PTC unrelated", subreddit="tax")
    with patch.object(pipeline.reddit, "search_reddit_in_subreddits", return_value=[]) as primary, \
         patch.object(pipeline.reddit_keyless, "search_public_in_subreddits", return_value=[public_post, outside_post]) as fallback, \
         patch.object(pipeline.reddit, "discover_subreddits") as discover:
        items, artifact = pipeline._retrieve_reddit_stream(
            topic="PTC", raw_topic="PTC", subquery=subquery,
            config={"SCRAPECREATORS_API_KEY": "dummy"}, depth="default",
            from_date="2026-07-01", to_date="2026-07-25", subreddits=None,
        )
    primary.assert_called_once_with(targeted_query, ["stocks"], depth="default", token="dummy")
    fallback.assert_called_once_with(targeted_query, ["stocks"], from_date="2026-07-01", to_date="2026-07-25", depth="default")
    discover.assert_not_called()
    assert items == [public_post]


def test_targeted_scope_validation_filters_primary_results_case_insensitively() -> None:
    subquery = schema.SubQuery(
        label="preferred-subreddit-daytrading", group_id="preferred_subreddit_ticker",
        search_query="PTC", ranking_query="PTC", sources=["reddit"],
        reddit_target_subreddits=["r/DayTrading", "Stocks"],
    )
    daytrading = _post("PTC daytrading", subreddit="r/DAYTRADING")
    stocks = _post("PTC stocks", subreddit="stocks")
    out_of_scope = [
        _post("Singapore", subreddit="singapore"),
        _post("Tax", subreddit="r/tax"),
        _post("Guns", subreddit="NJGuns"),
        _post("Missing subreddit", subreddit=""),
    ]
    malformed = _post("Malformed subreddit", subreddit="stocks")
    malformed.pop("subreddit")
    with patch.object(pipeline.reddit, "search_reddit_in_subreddits", return_value=[daytrading, stocks, *out_of_scope, malformed]) as primary, \
         patch.object(pipeline.reddit_keyless, "search_public_in_subreddits") as fallback:
        items, artifact = pipeline._retrieve_reddit_stream(
            topic="PTC", raw_topic="PTC", subquery=subquery,
            config={"SCRAPECREATORS_API_KEY": "dummy"}, depth="default",
            from_date="2026-07-01", to_date="2026-07-25", subreddits=None,
        )
    primary.assert_called_once_with("PTC", ["r/DayTrading", "Stocks"], depth="default", token="dummy")
    fallback.assert_not_called()
    assert items == [daytrading, stocks]
    diagnostics = artifact["reddit_search_diagnostics"]
    assert diagnostics["primary_search_usable"] == 1


def test_global_reddit_results_are_not_target_scope_filtered() -> None:
    subquery = schema.SubQuery(
        label="global", group_id="investment", search_query="PTC stock",
        ranking_query="PTC stock", sources=["reddit"],
    )
    singapore = _post("PTC global", subreddit="singapore")
    with patch.object(pipeline.reddit_public, "search_reddit_public", return_value=[singapore]):
        items, artifact = pipeline._retrieve_reddit_stream(
            topic="PTC", raw_topic="PTC", subquery=subquery, config={}, depth="default",
            from_date="2026-07-01", to_date="2026-07-25", subreddits=None,
        )
    assert items == [singapore]


def test_global_and_preferred_ticker_duplicate_is_merged_and_scored_once() -> None:
    plan = _plan(entity_terms=pipeline.company_title_eligibility_entities(
        ["PTC", "PTC Inc.", "Windchill"], "PTC",
    ))
    plan["subqueries"] = [
        {
            "label": "investment-1", "group_id": "investment_and_valuation",
            "search_query": "PTC stock", "ranking_query": "PTC stock", "sources": ["reddit"],
        },
        {
            "label": "preferred-subreddit-valueinvesting", "group_id": "preferred_subreddit_ticker",
            "search_query": "PTC", "ranking_query": "PTC", "sources": ["reddit"],
            "reddit_target_subreddits": ["valueinvesting"],
        },
    ]
    with patch.object(normalize, "PREFERRED_SUBREDDITS", {"valueinvesting"}):
        report = _run(plan, [_post("PTC earnings discussion", subreddit="ValueInvesting")], topic="PTC")
    assert report.artifacts["run_statistics"]["provisional_reddit_records_before_deduplication"] == 2
    assert report.artifacts["run_statistics"]["provisional_unique_reddit_posts_after_deduplication"] == 1
    assert len(report.ranked_candidates) == len(report.items_by_source["reddit"]) == 1
    candidate = report.ranked_candidates[0]
    primary = schema.candidate_primary_item(candidate)
    assert primary is not None
    base = 100.0 * rerank.signals.reddit_rank_score(
        (candidate.rerank_score or 0.0) / 100.0,
        candidate.freshness / 100.0,
        float(primary.metadata.get("reddit_normalized_comments") or 0.0),
        float(primary.metadata.get("reddit_normalized_post_score") or 0.0),
    )
    assert candidate.final_score == base * 1.5
    assert candidate.final_score != base * 2.25
    rendered = "\n".join(render._render_reddit_discussions(report.items_by_source["reddit"]))
    assert rendered.count("PTC earnings discussion") == 1
