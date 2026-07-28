"""Pure deterministic scheduling for grouped discovery expressions."""

from __future__ import annotations

from typing import Any


def schedule_group_queries(groups: list[dict[str, Any]], *, max_queries_per_group: int, max_queries_per_company: int) -> dict[str, list[dict[str, Any]]]:
    """Round-robin group queries without mutating the input records."""
    if max_queries_per_group < 1 or max_queries_per_company < 1:
        raise ValueError("query scheduler budgets must be positive integers")
    eligible: list[list[dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    for group in groups:
        queries = list(group["queries"])
        allowed, excess = queries[:max_queries_per_group], queries[max_queries_per_group:]
        eligible.append(allowed)
        skipped.extend({**query, "reason": "max_queries_per_group"} for query in excess)

    scheduled: list[dict[str, Any]] = []
    position = 0
    while len(scheduled) < max_queries_per_company:
        emitted = False
        for queue in eligible:
            if position < len(queue) and len(scheduled) < max_queries_per_company:
                scheduled.append(dict(queue[position]))
                emitted = True
        if not emitted:
            break
        position += 1
    for queue in eligible:
        for query in queue[position:]:
            skipped.append({**query, "reason": "max_queries_per_company"})
    return {"scheduled": scheduled, "skipped": skipped}
