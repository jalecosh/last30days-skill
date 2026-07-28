"""Stable engine run-summary contract, independent of rendering."""

from types import SimpleNamespace

import last30days as cli


def test_run_summary_uses_final_structured_candidate_list_and_reddit_artifacts():
    report = SimpleNamespace(
        ranked_candidates=[object(), object(), object()],
        artifacts={
            "run_statistics": {
                "raw_source_record_count": 20,
                "normalized_stream_item_count": 12,
                "deduplicated_candidate_count": 8,
                "ranked_candidate_count_before_final_reddit_cap": 5,
                "final_selected_count": 3,
            },
            "reddit_recall": {
                "raw_reddit_records_per_subquery": {"one": 9, "two": 11},
                "posts_removed_by_blocklist": 2,
                "posts_removed_as_commentless": 3,
                "posts_removed_as_zero_interaction": 4,
                "posts_with_usable_comments": 7,
                "final_rendered_post_count": 3,
            },
        },
    )
    summary = cli.build_run_summary(report)
    assert summary["final_selected_count"] == 3
    assert summary["reddit"] == {
        "raw_posts": 20,
        "posts_removed_by_blocklist": 2,
        "posts_removed_as_commentless": 3,
        "posts_removed_as_zero_engagement": 4,
        "posts_with_usable_comments": 7,
        "final_threads": 3,
    }
