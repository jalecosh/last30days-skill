from lib import pipeline, schema


def _candidate(name, groups, score):
    item = schema.SourceItem(
        item_id=name,
        source="reddit",
        title=name,
        body="body",
        url=f"https://reddit.com/r/test/comments/{name}/post/",
        engagement={"score": score, "num_comments": 1},
        metadata={"group_ids": groups, "top_comments": [{"excerpt": "usable", "score": 1}]},
    )
    return schema.Candidate(
        candidate_id=name,
        item_id=name,
        source="reddit",
        title=name,
        url=item.url,
        snippet="body",
        subquery_labels=[],
        native_ranks={},
        local_relevance=1,
        freshness=1,
        engagement=score,
        source_quality=1,
        rrf_score=1,
        source_items=[item],
        final_score=score,
    )


def _select(candidates):
    return pipeline._cap_final_reddit_posts(
        candidates,
        {"reddit": [candidate.source_items[0] for candidate in candidates]},
        company_group_allocation=pipeline.COMPANY_REDDIT_GROUP_ALLOCATION,
    )


def test_investment_groups_precede_higher_engagement_product_support():
    candidates = [
        *[_candidate(f"support-{index}", ["customer_and_product_evidence"], 100 - index) for index in range(10)],
        *[_candidate(f"investment-{index}", ["investment_and_valuation"], 50 - index) for index in range(8)],
        *[_candidate(f"business-{index}", ["business_performance"], 40 - index) for index in range(5)],
        *[_candidate(f"future-{index}", ["competition_and_future"], 30 - index) for index in range(5)],
    ]
    selected, items = _select(candidates)
    assert [candidate.title for candidate in selected] == [
        *[f"investment-{index}" for index in range(8)],
        *[f"business-{index}" for index in range(5)],
        "future-0", "future-1",
    ]
    assert len(items["reddit"]) == 15


def test_group_caps_and_duplicate_provenance_keep_customer_evidence_secondary():
    duplicate = _candidate("shared", ["investment_and_valuation", "customer_and_product_evidence"], 100)
    candidates = [
        duplicate,
        *[_candidate(f"business-{index}", ["business_performance"], 90 - index) for index in range(6)],
        *[_candidate(f"future-{index}", ["competition_and_future"], 80 - index) for index in range(6)],
        *[_candidate(f"management-{index}", ["management_and_internal"], 70 - index) for index in range(4)],
        *[_candidate(f"support-{index}", ["customer_and_product_evidence"], 60 - index) for index in range(5)],
    ]
    selected, items = _select(candidates)
    titles = [candidate.title for candidate in selected]
    assert titles.count("shared") == 1
    assert len(titles) == 15
    assert [title for title in titles if title.startswith("support-")] == ["support-0"]
    assert len({item.item_id for item in items["reddit"]}) == len(items["reddit"])


def test_empty_investment_groups_do_not_backfill_to_the_final_cap():
    candidates = [_candidate(f"support-{index}", ["customer_and_product_evidence"], 10 - index) for index in range(5)]
    selected, items = _select(candidates)
    assert [candidate.title for candidate in selected] == ["support-0", "support-1", "support-2"]
    assert len(items["reddit"]) == 3


def test_non_company_cap_keeps_existing_global_ranking_order():
    candidates = [_candidate(f"post-{index}", ["customer_and_product_evidence"], 30 - index) for index in range(20)]
    selected, _items = pipeline._cap_final_reddit_posts(
        candidates, {"reddit": [candidate.source_items[0] for candidate in candidates]},
    )
    assert [candidate.title for candidate in selected] == [f"post-{index}" for index in range(15)]