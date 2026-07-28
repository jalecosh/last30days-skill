"""Explicit pre/post comment-enrichment state contracts."""

from lib import normalize


def _raw(state=None, score=1, comments=1):
    item = {"id": "R1", "title": "Discussion", "url": "https://reddit.com/r/unlisted/comments/1/", "date": "2026-07-20", "subreddit": "Unlisted", "engagement": {"score": score, "num_comments": comments}}
    if state:
        item["comment_enrichment_state"] = state
    return item


def test_deferred_not_enriched_is_provisional_but_empty_and_failure_are_rejected():
    pending = normalize.normalize_source_items("reddit", [_raw(normalize.COMMENT_NOT_ENRICHED)], "2026-07-01", "2026-07-25", comment_validation="deferred")
    assert len(pending) == 1
    assert normalize.normalize_source_items("reddit", [_raw(normalize.COMMENT_ENRICHED_EMPTY)], "2026-07-01", "2026-07-25", comment_validation="deferred") == []
    assert normalize.normalize_source_items("reddit", [_raw(normalize.COMMENT_ENRICHMENT_FAILED)], "2026-07-01", "2026-07-25", comment_validation="immediate") == []


def test_legacy_immediate_mode_accepts_only_usable_comment_evidence():
    usable = _raw(normalize.COMMENT_ENRICHED_USABLE)
    usable["top_comments"] = [{"excerpt": "usable"}]
    assert len(normalize.normalize_source_items("reddit", [usable], "2026-07-01", "2026-07-25")) == 1
    assert normalize.normalize_source_items("reddit", [_raw(normalize.COMMENT_NOT_ENRICHED)], "2026-07-01", "2026-07-25") == []
