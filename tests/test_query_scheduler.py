"""Generic deterministic group-query scheduler contracts."""

from tools.query_scheduler import schedule_group_queries


def _groups():
    return [
        {"group_id": "group-alpha", "queries": [{"group_id": "group-alpha", "label": "alpha-1", "query": "one"}, {"group_id": "group-alpha", "label": "alpha-2", "query": "two"}, {"group_id": "group-alpha", "label": "alpha-3", "query": "three"}]},
        {"group_id": "group-beta", "queries": [{"group_id": "group-beta", "label": "beta-1", "query": "four"}, {"group_id": "group-beta", "label": "beta-2", "query": "five"}]},
        {"group_id": "group-gamma", "queries": [{"group_id": "group-gamma", "label": "gamma-1", "query": "six"}]},
    ]


def test_round_robin_preserves_group_and_query_order_without_mutation():
    groups = _groups()
    result = schedule_group_queries(groups, max_queries_per_group=6, max_queries_per_company=30)
    assert [row["label"] for row in result["scheduled"]] == ["alpha-1", "beta-1", "gamma-1", "alpha-2", "beta-2", "alpha-3"]
    assert result["skipped"] == []
    assert [row["label"] for row in groups[0]["queries"]] == ["alpha-1", "alpha-2", "alpha-3"]


def test_group_and_company_budgets_record_explicit_reasons():
    result = schedule_group_queries(_groups(), max_queries_per_group=2, max_queries_per_company=3)
    assert [row["label"] for row in result["scheduled"]] == ["alpha-1", "beta-1", "gamma-1"]
    assert {(row["label"], row["reason"]) for row in result["skipped"]} == {("alpha-3", "max_queries_per_group"), ("alpha-2", "max_queries_per_company"), ("beta-2", "max_queries_per_company")}


def test_one_group_behaves_in_declared_order_and_is_deterministic():
    groups = [{"group_id": "group-alpha", "queries": [{"group_id": "group-alpha", "label": "alpha-1", "query": "one"}, {"group_id": "group-alpha", "label": "alpha-2", "query": "two"}]}]
    first = schedule_group_queries(groups, max_queries_per_group=6, max_queries_per_company=30)
    assert first == schedule_group_queries(groups, max_queries_per_group=6, max_queries_per_company=30)
    assert [row["label"] for row in first["scheduled"]] == ["alpha-1", "alpha-2"]
