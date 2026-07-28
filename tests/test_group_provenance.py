"""Generic group provenance survives flat-plan execution and fusion."""

from lib import fusion, pipeline, planner, schema


def _item() -> schema.SourceItem:
    return schema.SourceItem(
        item_id="post-1", source="reddit", title="Post", body="body", url="https://reddit.com/r/test/comments/abc/post",
        local_relevance=0.8, freshness=70, source_quality=0.6, engagement_score=10,
    )


def test_old_plan_without_group_id_remains_valid():
    plan = planner._sanitize_plan(
        {"subqueries": [{"label": "legacy", "search_query": "term", "ranking_query": "rank", "sources": ["reddit"]}]},
        "topic", ["reddit"], ["reddit"], "default",
    )
    assert plan.subqueries[0].group_id is None
    assert plan.reddit_entity_terms == []


def test_external_company_plan_preserves_reddit_entity_terms_for_title_gate():
    plan = planner._sanitize_plan(
        {
            "reddit_entity_terms": ["Autodesk", "Autodesk, Inc."],
            "reddit_comment_enrichment_mode": "deferred_company_wide",
            "reddit_search_execution_mode": "group_scoped_subreddit_expansion",
            "company_query_budget": 1,
            "preserve_all_subqueries": True,
            "subqueries": [{"label": "company-1", "group_id": "company", "search_query": "Autodesk", "ranking_query": "Autodesk", "sources": ["reddit"]}],
        },
        "ADSK", ["reddit"], ["reddit"], "default",
    )
    assert plan.reddit_entity_terms == ["Autodesk", "Autodesk, Inc."]
    assert pipeline.matches_company_entity("Autodesk audit", "", plan.reddit_entity_terms)
    assert plan.reddit_comment_enrichment_mode == "deferred_company_wide"
    assert plan.reddit_search_execution_mode == "group_scoped_subreddit_expansion"
    assert plan.reddit_comment_tree_budget == 24
    assert plan.subqueries[0].group_id == "company"
    assert plan.subqueries[0].sources == ["reddit"]


def test_blank_and_non_string_reddit_entity_terms_are_ignored():
    plan = planner._sanitize_plan(
        {"reddit_entity_terms": [" Autodesk ", "", "  ", None, 42], "subqueries": [{"label": "q", "search_query": "Autodesk", "ranking_query": "Autodesk", "sources": ["reddit"]}]},
        "ADSK", ["reddit"], ["reddit"], "default",
    )
    assert plan.reddit_entity_terms == ["Autodesk"]


def test_group_id_survives_sanitization_and_fusion_merges_provenance():
    plan = planner._sanitize_plan(
        {"subqueries": [
            {"label": "alpha-1", "group_id": "group-alpha", "search_query": "first expression", "ranking_query": "rank", "sources": ["reddit"]},
            {"label": "beta-1", "group_id": "group-beta", "search_query": "second expression", "ranking_query": "rank", "sources": ["reddit"]},
        ]},
        "topic", ["reddit"], ["reddit"], "default",
    )
    candidates = fusion.weighted_rrf({("alpha-1", "reddit"): [_item()], ("beta-1", "reddit"): [_item()]}, plan, pool_limit=10)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.subquery_labels == ["alpha-1", "beta-1"]
    assert candidate.metadata["group_ids"] == ["group-alpha", "group-beta"]
    assert candidate.metadata["search_queries"] == ["first expression", "second expression"]


def test_group_provenance_reaches_normalized_item_metadata():
    item = _item()
    subquery = schema.SubQuery(label="alpha-1", group_id="group-alpha", search_query="first expression", ranking_query="rank", sources=["reddit"])
    pipeline._attach_subquery_provenance(item, subquery)
    assert item.metadata == {
        "group_ids": ["group-alpha"], "subquery_labels": ["alpha-1"], "search_queries": ["first expression"],
    }
