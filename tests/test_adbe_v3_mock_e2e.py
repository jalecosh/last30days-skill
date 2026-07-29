"""Mocked ADBE V3 integration: grouped discovery through final structured report."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

from lib import pipeline


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("research_company_e2e", ROOT / "tools" / "research_company.py")
research_company = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = research_company
assert SPEC.loader
SPEC.loader.exec_module(research_company)


def test_adbe_grouped_mock_pipeline_keeps_all_base_queries_and_budgeted_unique_posts():
    resolved = research_company.resolve_run(research_company.normalize_ticker("ADBE"))
    plan, scheduled, _ = research_company.build_engine_plan(resolved)
    submitted = []

    def retrieve(**kwargs):
        subquery = kwargs["subquery"]
        submitted.append(subquery.label)
        # One post repeats in every group/query; the remaining post is unique.
        shared = {
            "id": "R1", "reddit_id": "sharedpost", "title": "Adobe shared discussion",
            "url": "https://old.reddit.com/r/fictional/comments/sharedpost/thread/?utm=x",
            "date": "2026-07-20", "subreddit": "fictional", "selftext": "Adobe", "relevance": 0.9,
            "engagement": {"score": 10, "num_comments": 5}, "comment_enrichment_state": "not_enriched",
        }
        unique_id = subquery.label.replace("-", "")
        unique = {**shared, "reddit_id": unique_id, "url": f"https://www.reddit.com/r/fictional/comments/{unique_id}/thread/", "title": subquery.search_query}
        return [shared, unique], {"reddit_search_diagnostics": {"primary_backend": "mock", "primary_search_attempts": 1, "primary_search_usable": 1, "primary_search_empty": 0, "primary_search_failed": 0, "public_fallback_invocations": 0, "accepted_record_count": 2}}

    with patch("lib.pipeline._retrieve_stream", side_effect=retrieve), \
         patch("lib.pipeline.reddit.discover_subreddits", return_value=[]), \
         patch("lib.pipeline.reddit_keyless.enrich_public_post_comments", return_value={"top_comments": [{"excerpt": "mock comment", "score": 1}]}), \
         patch("lib.pipeline._retry_thin_sources"), \
         patch("lib.pipeline._run_supplemental_searches"), \
         patch("lib.pipeline._load_library_context", return_value=(None, None)):
        report = pipeline.run(topic="Adobe", config={}, depth="default", requested_sources=["reddit"], mock=True, external_plan=plan)

    assert submitted == [query["label"] for query in scheduled]
    stats = report.artifacts["run_statistics"]
    group = report.artifacts["reddit_group_search"]
    enrichment = report.artifacts["reddit_comment_enrichment"]
    assert len(resolved["config"]["search_groups"]) == 5
    assert len(submitted) == len(scheduled) == 39
    assert "subreddit_discovery_groups_processed" not in group
    assert stats["provisional_reddit_records_before_deduplication"] > stats["provisional_unique_reddit_posts_after_deduplication"]
    assert enrichment["posts_selected_for_comment_enrichment"] <= 24
    assert stats["posts_pending_comment_enrichment"] == enrichment["posts_skipped_by_comment_tree_budget"]
    assert enrichment["posts_enriched_usable"] + enrichment["posts_enriched_empty"] + enrichment["posts_enrichment_failed"] == enrichment["posts_selected_for_comment_enrichment"]
    assert group["base_search_primary_attempts"] == len(submitted)
    assert report.ranked_candidates
