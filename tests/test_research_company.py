"""Ticker-first company runner integration contracts."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from lib import pipeline, planner


MODULE_PATH = Path(__file__).parents[1] / "tools" / "research_company.py"
SPEC = importlib.util.spec_from_file_location("research_company", MODULE_PATH)
assert SPEC and SPEC.loader
research_company = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = research_company
SPEC.loader.exec_module(research_company)

@pytest.fixture(autouse=True)
def mock_company_identity_lookups(monkeypatch):
    companies = {
        "ADBE": ("Adobe Inc.", "Adobe Inc."),
        "ADSK": ("Autodesk, Inc.", "Autodesk, Inc."),
        "PTC": ("PTC Inc.", "PTC Inc."),
        "ISRG": ("Intuitive Surgical, Inc.", "Intuitive Surgical, Inc."),
        "SPGI": ("S&P Global Inc.", "S&P Global Inc."),
    }

    def yahoo(query):
        company = companies.get(query.upper())
        if company is None:
            return {"quotes": []}
        longname, shortname = company
        return {"quotes": [{
            "symbol": query.upper(), "quoteType": "EQUITY",
            "longname": longname, "shortname": shortname,
            "exchange": "NMS", "industry": "Software",
        }]}

    monkeypatch.setattr(research_company, "_yahoo", yahoo)


def _resolved(ticker: str = "ADBE"):
    return research_company.resolve_run(research_company.normalize_ticker(ticker))


@pytest.mark.parametrize("ticker", ["ADBE", "ADSK", "PTC"])
def test_every_company_loads_the_same_global_blocklist(ticker):
    policy = _resolved(ticker)["reddit_policy"]
    assert policy.blocklist_path == str(research_company.BLOCKLIST_PATH)
    assert policy.blocklist_version == 1
    assert policy.blocked_subreddits


def test_generic_company_path_requires_no_company_config_file():
    resolved = _resolved("ADSK")
    plan = research_company.complete_plan(research_company.normalize_ticker("ADSK"), resolved, date(2026, 7, 26), 30)
    assert plan["resolved_company_name"]
    assert plan["resolved_short_name"]


def test_ticker_configs_cannot_affect_generic_plans(monkeypatch):
    contradictory = "config/companies/ADBE.json"
    reads: list[Path] = []
    original_read_text = Path.read_text

    def reject_company_config_reads(path, *args, **kwargs):
        if path.as_posix().endswith(contradictory):
            reads.append(path)
            raise AssertionError("ticker-specific config must never be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_company_config_reads)
    plan = research_company.complete_plan(research_company.normalize_ticker("ADBE"), _resolved("ADBE"), date(2026, 7, 26), 30)
    assert not reads
    assert plan["research_group_count"] == 5
    assert plan["query_scheduler"]["scheduled_query_count"] == 27
    assert "Not Adobe" not in plan["engine_plan"]["reddit_entity_terms"]
    assert "Not A Product" not in plan["engine_plan"]["reddit_entity_terms"]


@pytest.mark.parametrize("ticker", ["ADBE", "ADSK", "ISRG", "SPGI"])
def test_all_companies_use_the_same_generic_five_group_27_query_model(ticker):
    resolved = _resolved(ticker)
    plan = research_company.complete_plan(research_company.normalize_ticker(ticker), resolved, date(2026, 7, 26), 30)
    assert [group["id"] for group in plan["resolved_search_groups"]] == [
        "investment_and_valuation", "business_performance", "management_and_internal",
        "competition_and_future", "customer_and_product_evidence",
    ]
    assert plan["research_group_count"] == 5
    assert plan["query_scheduler"]["scheduled_query_count"] == 27
    assert "company_config_path" not in plan
    assert set(plan["engine_plan"]["reddit_entity_terms"]) <= set(resolved["entities"])

def test_ticker_normalization_and_output_filenames_are_ticker_led():
    assert research_company.normalize_ticker("nasdaq:adbe").display == "NASDAQ:ADBE"
    plan = research_company.complete_plan(research_company.normalize_ticker("ADBE"), _resolved(), date(2026, 7, 26), 30)
    assert Path(plan["markdown_output_path"]).name == "ADBE-reddit-2026-06-27-to-2026-07-26.md"
    assert Path(plan["metadata_output_path"]).name == "ADBE-reddit-2026-06-27-to-2026-07-26.json"
    assert plan["global_reddit_blocklist"]["path"] == str(research_company.BLOCKLIST_PATH)


def test_company_plans_use_generic_deferred_comment_mode_with_bounded_schedule():
    for ticker in ("ADBE", "ADSK"):
        resolved = _resolved(ticker)
        plan = research_company.complete_plan(research_company.normalize_ticker(ticker), resolved, date(2026, 7, 26), 30)
        budget = resolved["config"]["reddit_execution"]
        assert plan["engine_plan"]["reddit_comment_enrichment_mode"] == "deferred_company_wide"
        assert plan["comment_enrichment"]["maximum_unique_comment_tree_requests"] == budget["max_comment_trees_per_company"]
        assert plan["query_scheduler"]["scheduled_query_count"] == 27
        assert plan["query_scheduler"]["targeted_preferred_subreddit_query_count"] == len(plan["global_reddit_preferences"]["preferred_subreddits"])
        assert len(plan["queries"]) == 27 + len(plan["global_reddit_preferences"]["preferred_subreddits"])
        assert plan["execution_estimate"]["reddit_subquery_count"] == len(plan["queries"])
        global_queries = [query for query in plan["queries"] if not query.get("reddit_target_subreddits")]
        assert all(any(entity.casefold() in query["query"].casefold() for entity in resolved["entities"]) for query in global_queries)


def test_cli_overrides_win_over_global_defaults():
    result = research_company.resolve_run(research_company.normalize_ticker("ADBE"), {"default_days": 14, "reddit_backend": "scrapecreators"})["config"]
    assert result["default_days"] == 14
    assert result["reddit_backend"] == "scrapecreators"


def test_generic_groups_have_unique_ids_without_company_config():
    resolved = _resolved("ADSK")
    groups = resolved["config"]["search_groups"]
    assert len({group["id"] for group in groups}) == len(groups)
    assert all(group["queries"] for group in groups)


def test_mock_execution_uses_structured_summary_not_markdown(monkeypatch):
    markdown, metadata, _, _ = research_company.result_paths("ADSK", "output/results", date(2026, 1, 1), 1)
    for stale_summary in markdown.parent.glob(".ADSK-summary-*.json"):
        stale_summary.unlink()
    calls = []

    class Completed:
        returncode = 0

    summary = {
        "schema_version": 1, "execution_status": "completed", "final_selected_count": 7,
        "reddit": {"posts_removed_by_blocklist": 3},
    }

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--output") + 1]).write_text("## Any presentation heading\n", encoding="utf-8")
        Path(command[command.index("--run-summary-output") + 1]).write_text(json.dumps(summary), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(research_company.subprocess, "run", fake_run)
    try:
        assert research_company.main(["ADSK", "--days", "1", "--as-of", "2026-01-01"]) == 0
        command, kwargs = calls[0]
        assert "--subreddits" not in command and "--dedicated-subreddits" not in command
        assert kwargs["env"]["LAST30DAYS_REDDIT_BLOCKLIST_PATH"] == str(research_company.BLOCKLIST_PATH)
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        assert payload["global_reddit_blocklist"]["version"] == 1
        assert payload["statistics"]["final_selected_count"] == 7
        assert payload["statistics"]["reddit"]["posts_removed_by_blocklist"] == 3
        assert not list(markdown.parent.glob(".ADSK-summary-*.json"))
    finally:
        markdown.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


@pytest.mark.parametrize("heading", ["## H2 layout", "### 99. H3 layout"])
def test_markdown_heading_format_has_no_effect_on_metadata_counts(monkeypatch, heading):
    markdown, metadata, _, _ = research_company.result_paths("ADSK", "output/results", date(2026, 1, 2), 1)

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        Path(command[command.index("--output") + 1]).write_text(heading, encoding="utf-8")
        Path(command[command.index("--run-summary-output") + 1]).write_text(
            '{"schema_version": 1, "execution_status": "completed", "final_selected_count": 4, "reddit": {}}', encoding="utf-8"
        )
        return Completed()

    monkeypatch.setattr(research_company.subprocess, "run", fake_run)
    try:
        research_company.main(["ADSK", "--days", "1", "--as-of", "2026-01-02"])
        assert json.loads(metadata.read_text(encoding="utf-8"))["statistics"]["final_selected_count"] == 4
    finally:
        markdown.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def test_failed_execution_without_summary_writes_failed_metadata(monkeypatch):
    markdown, metadata, _, _ = research_company.result_paths("ADSK", "output/results", date(2026, 1, 3), 1)

    class Failed:
        returncode = 1

    monkeypatch.setattr(research_company.subprocess, "run", lambda *args, **kwargs: Failed())
    try:
        assert research_company.main(["ADSK", "--days", "1", "--as-of", "2026-01-03"]) == 1
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        assert payload["execution_status"] == "failed"
        assert payload["statistics"] is None
        assert "did not write" in payload["run_summary_error"]
    finally:
        markdown.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def test_malformed_run_summary_is_reported(monkeypatch):
    markdown, metadata, _, _ = research_company.result_paths("ADSK", "output/results", date(2026, 1, 4), 1)

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        Path(command[command.index("--run-summary-output") + 1]).write_text("not json", encoding="utf-8")
        return Completed()

    monkeypatch.setattr(research_company.subprocess, "run", fake_run)
    try:
        research_company.main(["ADSK", "--days", "1", "--as-of", "2026-01-04"])
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        assert payload["statistics"] is None
        assert "malformed" in payload["run_summary_error"]
    finally:
        markdown.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def test_execution_estimate_covers_every_scheduled_generic_query():
    resolved = _resolved("ADBE")
    plan = research_company.complete_plan(research_company.normalize_ticker("ADBE"), resolved, date(2026, 7, 26), 30)
    estimate = plan["execution_estimate"]
    assert estimate["reddit_subquery_count"] == len(plan["queries"])
    assert estimate["scheduled_base_query_count"] == 27
    assert estimate["preferred_subreddit_ticker_query_count"] == len(plan["global_reddit_preferences"]["preferred_subreddits"])
    assert estimate["queries_expected_to_be_skipped"] == []
    assert estimate["source_fetch_cap"] is None
    assert estimate["submission_order"] == [query["label"] for query in plan["queries"]]
    assert plan["research_group_count"] == len(plan["resolved_search_groups"])
    assert plan["configured_query_count"] == 27
    assert len(plan["groups"]) == plan["research_group_count"]
    assert plan["query_scheduler"]["scheduled_query_count"] == 27
    assert plan["query_scheduler"]["scheduled_query_count"] <= resolved["config"]["reddit_execution"]["max_queries_per_company"]
    assert plan["query_scheduler"]["skipped_query_count"] == 0


def test_adsk_uses_resolved_identity_in_generic_groups():
    resolved = _resolved("ADSK")
    plan = research_company.complete_plan(research_company.normalize_ticker("ADSK"), resolved, date(2026, 7, 26), 30)
    group_ids = {group["id"] for group in plan["resolved_search_groups"]}
    assert plan["research_group_count"] == len(group_ids)
    global_subqueries = [subquery for subquery in plan["engine_plan"]["subqueries"] if not subquery.get("reddit_target_subreddits")]
    global_queries = [query for query in plan["queries"] if not query.get("reddit_target_subreddits")]
    assert all(subquery["group_id"] in group_ids for subquery in global_subqueries)
    assert all(resolved["identity"].short_name.casefold() in query["query"].casefold() for query in global_queries)
    assert all("d-wave" not in query["query"].casefold() for query in plan["queries"])


@pytest.mark.parametrize(("ticker", "expected_query"), [
    ("ADSK", 'ADSK OR "Autodesk"'),
    ("SPGI", 'SPGI OR "S&P Global"'),
])
def test_preferred_subreddit_lane_uses_resolved_ticker_and_short_name(ticker, expected_query):
    plan = research_company.complete_plan(research_company.normalize_ticker(ticker), _resolved(ticker), date(2026, 7, 26), 30)
    targeted = [query for query in plan["queries"] if query.get("reddit_target_subreddits")]
    preferred = plan["global_reddit_preferences"]["preferred_subreddits"]
    global_queries = [query for query in plan["queries"] if not query.get("reddit_target_subreddits")]
    assert len(global_queries) == 27
    assert len(targeted) == len(preferred) == 9
    assert len(plan["queries"]) == 36
    assert [query["query"] for query in targeted] == [expected_query] * len(preferred)
    assert [query["reddit_target_subreddits"] for query in targeted] == [[subreddit] for subreddit in preferred]
    assert "stockmarket" in {subreddit for query in targeted for subreddit in query["reddit_target_subreddits"]}
    assert plan["execution_estimate"]["maximum_subreddit_discovery_passes"] == 0
    assert plan["execution_estimate"]["maximum_subreddit_expansion_requests"] == 0


def test_preferred_subreddit_lane_avoids_duplicate_ticker_and_falls_back_without_short_name():
    config = research_company._deep_merge(research_company._read_object(research_company.DEFAULTS_PATH), {
        "company_name": "PTC Inc.",
        "search_groups": research_company._generic_groups(research_company.CompanyIdentity("PTC", "PTC Inc.", "PTC")),
    })
    policy = research_company.reddit_policy.Policy(
        None, None, frozenset(), 1, "fixture", frozenset({"stocks"}),
    )
    for identity, expected_query in (
        (research_company.CompanyIdentity("PTC", "PTC Inc.", "  ptc  "), "PTC"),
        (research_company.CompanyIdentity("EXMP", "Example Systems, Inc.", ""), "EXMP"),
    ):
        _engine_plan, queries, _scheduler = research_company.build_engine_plan({
            "config": config, "identity": identity, "entities": [], "reddit_policy": policy,
        })
        targeted = [query for query in queries if query.get("reddit_target_subreddits")]
        assert [query["query"] for query in targeted] == [expected_query]


def test_missing_preference_policy_creates_no_targeted_subreddit_searches():
    identity = research_company.CompanyIdentity("PTC", "PTC Inc.", "PTC")
    config = research_company._deep_merge(research_company._read_object(research_company.DEFAULTS_PATH), {
        "company_name": identity.company_name,
        "search_groups": research_company._generic_groups(identity),
    })
    _engine_plan, queries, scheduler = research_company.build_engine_plan({
        "config": config, "identity": identity, "entities": ["PTC Inc."],
    })
    assert scheduler["targeted_preferred_subreddit_query_count"] == 0
    assert len(queries) == 27
    assert not any(query.get("reddit_target_subreddits") for query in queries)


def test_policy_subreddit_deduplication_prevents_duplicate_targeted_requests():
    identity = research_company.CompanyIdentity("PTC", "PTC Inc.", "PTC")
    config = research_company._deep_merge(research_company._read_object(research_company.DEFAULTS_PATH), {
        "company_name": identity.company_name,
        "search_groups": research_company._generic_groups(identity),
    })
    policy = research_company.reddit_policy.Policy(
        None, None, frozenset(), 1, "fixture", frozenset({"stocks", "stockmarket"}),
    )
    _engine_plan, queries, _scheduler = research_company.build_engine_plan({
        "config": config, "identity": identity, "entities": ["PTC Inc."], "reddit_policy": policy,
    })
    targeted = [query for query in queries if query.get("reddit_target_subreddits")]
    assert [query["reddit_target_subreddits"] for query in targeted] == [["stockmarket"], ["stocks"]]


def test_external_company_plan_preserves_all_global_and_targeted_subqueries_through_sanitization():
    resolved = _resolved("PTC")
    engine_plan, queries, _scheduler = research_company.build_engine_plan(resolved)
    sanitized = planner._sanitize_plan(engine_plan, "PTC", ["reddit"], ["reddit"], "default")
    global_subqueries = [query for query in sanitized.subqueries if not query.reddit_target_subreddits]
    targeted_subqueries = [query for query in sanitized.subqueries if query.reddit_target_subreddits]
    assert len(queries) == len(sanitized.subqueries) == 36
    assert len(global_subqueries) == 27
    assert len(targeted_subqueries) == 9
    assert all(len(query.reddit_target_subreddits) == 1 for query in targeted_subqueries)
    assert {query.reddit_target_subreddits[0] for query in targeted_subqueries} == set(
        resolved["reddit_policy"].preferred_subreddits
    )


def test_sanitizer_keeps_ordinary_external_plan_cap_and_rejects_malformed_company_subqueries():
    ordinary = {
        "subqueries": [
            {"label": f"q{index}", "search_query": f"term {index}", "ranking_query": "term", "sources": ["reddit"]}
            for index in range(10)
        ]
    }
    capped = planner._sanitize_plan(ordinary, "topic", ["reddit"], ["reddit"], "default")
    assert len(capped.subqueries) < len(ordinary["subqueries"])

    resolved = _resolved("PTC")
    engine_plan, _queries, _scheduler = research_company.build_engine_plan(resolved)
    engine_plan["subqueries"].append({
        "label": "bad-target", "group_id": "preferred_subreddit_ticker",
        "search_query": "PTC", "ranking_query": "PTC", "sources": ["reddit"],
        "reddit_target_subreddits": ["not/a/subreddit"],
    })
    sanitized = planner._sanitize_plan(engine_plan, "PTC", ["reddit"], ["reddit"], "default")
    assert len(sanitized.subqueries) == 36
    assert "bad-target" not in {query.label for query in sanitized.subqueries}


def test_sanitized_company_plan_reaches_all_targeted_native_reddit_branches():
    resolved = _resolved("PTC")
    engine_plan, _queries, _scheduler = research_company.build_engine_plan(resolved)
    submitted = []

    def retrieve(**kwargs):
        submitted.append(kwargs["subquery"])
        return [], {}

    with patch("lib.pipeline._retrieve_stream", side_effect=retrieve), \
         patch("lib.pipeline.enrich_provisional_reddit_items", return_value=([], {"posts_skipped_by_comment_tree_budget": 0})), \
         patch("lib.pipeline._retry_thin_sources"), \
         patch("lib.pipeline._run_supplemental_searches"), \
         patch("lib.pipeline._load_library_context", return_value=(None, None)):
        pipeline.run(
            topic="PTC", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"}, depth="default",
            requested_sources=["reddit"], mock=True, external_plan=engine_plan,
        )
    targeted = [query for query in submitted if query.reddit_target_subreddits]
    assert len(submitted) == 36
    assert len(targeted) == 9

    with patch.object(pipeline.reddit, "search_reddit_in_subreddits", return_value=[]) as primary, \
         patch.object(pipeline.reddit_keyless, "search_public_in_subreddits", return_value=[]):
        for subquery in targeted:
            pipeline._retrieve_reddit_stream(
                topic="PTC", raw_topic="PTC", subquery=subquery,
                config={"SCRAPECREATORS_API_KEY": "dummy"}, depth="default",
                from_date="2026-07-01", to_date="2026-07-29", subreddits=None,
            )
    assert primary.call_count == 9
    assert {call.args[1][0] for call in primary.call_args_list} == set(resolved["reddit_policy"].preferred_subreddits)


def test_execution_estimate_is_generic_and_deterministic():
    engine_plan = {"subqueries": [
        {"label": "group-a-1", "sources": ["reddit"]},
        {"label": "group-b-1", "sources": ["reddit"]},
        {"label": "other-1", "sources": ["youtube"]},
    ]}
    queries = [
        {"label": "group-a-1", "group_id": "group-a", "query": "first"},
        {"label": "group-b-1", "group_id": "group-b", "query": "second"},
        {"label": "other-1", "group_id": "other", "query": "third"},
    ]
    estimate = research_company.build_execution_estimate(engine_plan, queries)
    assert estimate["reddit_subquery_count"] == 2
    assert estimate["queries_expected_to_execute"] == ["group-a-1", "group-b-1"]
    assert estimate["queries_expected_to_be_skipped"] == []


def test_mock_adsk_plan_uses_bounded_generic_investment_groups():
    identity = research_company.CompanyIdentity("ADSK", "Autodesk, Inc.", "Autodesk")
    config = research_company._deep_merge(research_company._read_object(research_company.DEFAULTS_PATH), {
        "company_name": identity.company_name,
        "search_groups": research_company._generic_groups(identity),
    })
    engine_plan, queries, scheduler = research_company.build_engine_plan({
        "config": config, "identity": identity, "entities": ["Autodesk", "Autodesk, Inc."],
    })
    groups = {group["id"]: group["queries"] for group in config["search_groups"]}
    assert groups == {
        "investment_and_valuation": ["Autodesk stock", "Autodesk valuation", "Autodesk earnings", "Autodesk free cash flow", "Autodesk buy sell hold", "Autodesk bull case bear case"],
        "business_performance": ["Autodesk revenue growth", "Autodesk margins", "Autodesk pricing power", "Autodesk subscription growth", "Autodesk customer growth", "Autodesk business model"],
        "management_and_internal": ["Autodesk management", "Autodesk layoffs", "Autodesk employees", "Autodesk company culture", "Autodesk acquisition", "Autodesk capital allocation"],
        "competition_and_future": ["Autodesk moat", "Autodesk competition", "Autodesk market share", "Autodesk switching costs", "Autodesk AI risk", "Autodesk future growth"],
        "customer_and_product_evidence": ["Autodesk customer complaints", "Autodesk alternatives", "Autodesk workflow"],
    }
    planned_queries = [query["query"] for query in queries if not query.get("reddit_target_subreddits")]
    assert len(planned_queries) == scheduler["scheduled_query_count"] == 27 <= config["reddit_execution"]["max_queries_per_company"]
    assert len(groups["customer_and_product_evidence"]) == 3
    assert engine_plan["reddit_entity_terms"] == ["Autodesk", "Autodesk, Inc."]
    assert all("adsk" != query.casefold().strip() and "d-wave" not in query.casefold() for query in planned_queries)
    forbidden = ("deployment", "installation", "tutorial", "sample project", "licensing error")
    assert not any(term in query.casefold() for query in planned_queries for term in forbidden)
def test_generic_groups_remain_company_agnostic_without_company_configuration():
    identity = research_company.CompanyIdentity("EXMP", "Example Systems, Inc.", "Example Systems")
    groups = research_company._generic_groups(identity)
    assert [group["id"] for group in groups] == [
        "investment_and_valuation", "business_performance", "management_and_internal",
        "competition_and_future", "customer_and_product_evidence",
    ]
    assert all("Example Systems" in query for group in groups for query in group["queries"])

def test_engine_plan_excludes_only_the_exact_ticker_from_title_eligibility():
    identity = research_company.CompanyIdentity("PTC", "PTC Inc.", "PTC")
    config = research_company._deep_merge(research_company._read_object(research_company.DEFAULTS_PATH), {
        "company_name": identity.company_name,
        "search_groups": research_company._generic_groups(identity),
    })
    engine_plan, queries, scheduler = research_company.build_engine_plan({
        "config": config,
        "identity": identity,
        "entities": ["PTC", "PTC Inc.", "Creo", "Windchill"],
    })
    assert engine_plan["reddit_entity_terms"] == ["PTC Inc.", "Creo", "Windchill"]
    assert len([query for query in queries if not query.get("reddit_target_subreddits")]) == scheduler["scheduled_query_count"] == 27
