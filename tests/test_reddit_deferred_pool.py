"""Generic Phase 3B discovery-only contracts; no network calls."""

from unittest import mock

from lib import env, pipeline, schema


def _item(post_id: str, url: str, *, group: str, label: str, query: str, score: int = 1) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=f"R-{post_id}", source="reddit", title="fictional post", body="body",
        url=url, container="fictional", published_at="2026-07-20",
        engagement={"score": score, "num_comments": score}, metadata={
            "reddit_post_id": post_id, "comment_enrichment_state": "not_enriched",
            "group_ids": [group], "subquery_labels": [label], "search_queries": [query],
        },
    )


def test_provisional_pool_merges_identity_and_all_provenance():
    first = _item("abc", "https://www.reddit.com/r/fictional/comments/abc/one/?utm=x", group="group-alpha", label="alpha-1", query="first", score=2)
    second = _item("abc", "https://old.reddit.com/r/fictional/comments/abc/two/", group="group-beta", label="beta-1", query="second", score=9)
    merged = pipeline.merge_provisional_reddit_items([first, second])
    assert len(merged) == 1
    assert merged[0].metadata["group_ids"] == ["group-alpha", "group-beta"]
    assert merged[0].metadata["subquery_labels"] == ["alpha-1", "beta-1"]
    assert merged[0].metadata["search_queries"] == ["first", "second"]
    assert merged[0].engagement["score"] == 9
    assert merged[0].metadata["provisional_records_merged"] == 2


def test_canonical_reddit_url_normalizes_host_query_fragment_and_comment_title():
    left = _item("", "http://www.reddit.com/r/fictional/comments/post-1/title/?x=1#part", group="group-alpha", label="a", query="a")
    right = _item("", "https://old.reddit.com/r/fictional/comments/post-1/other-title", group="group-beta", label="b", query="b")
    assert pipeline.canonical_reddit_identity(left) == pipeline.canonical_reddit_identity(right)
    assert len(pipeline.merge_provisional_reddit_items([left, right])) == 1


def test_deferred_dispatch_skips_comment_enrichment_but_keeps_backend_order():
    subquery = schema.SubQuery(label="alpha-1", search_query="fictional", ranking_query="fictional", sources=["reddit"], group_id="group-alpha")
    config = {
        "SCRAPECREATORS_API_KEY": "dummy", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
        "_reddit_comment_enrichment_mode": "deferred_company_wide",
    }
    with mock.patch("lib.reddit.search_and_enrich", return_value={"items": []}) as primary, \
         mock.patch("lib.reddit.parse_reddit_response", return_value=[{"id": "R1", "comment_enrichment_state": "not_enriched"}]), \
         mock.patch("lib.reddit_public.search_reddit_public") as public:
        items, _ = pipeline._retrieve_reddit_stream(topic="fictional", subquery=subquery, config=config, depth="quick", from_date="2026-07-01", to_date="2026-07-25", raw_topic="fictional", subreddits=None)
    assert items and items[0]["comment_enrichment_state"] == "not_enriched"
    assert primary.call_args.kwargs["comment_enrichment"] is False
    public.assert_not_called()


def test_legacy_plan_defaults_to_immediate_comment_mode():
    plan = schema.query_plan_from_dict({
        "intent": "opinion", "freshness_mode": "strict_recent", "cluster_mode": "story",
        "raw_topic": "fictional", "source_weights": {"reddit": 1.0},
        "subqueries": [{"label": "a", "search_query": "fictional", "ranking_query": "fictional", "sources": ["reddit"]}],
    })
    assert plan.reddit_comment_enrichment_mode == "immediate"


def test_temporary_stream_ids_are_not_permanent_reddit_identity():
    raw_one = {"id": "R1", "reddit_id": "R1", "title": "one", "url": "", "date": "2026-07-20", "subreddit": "fictional", "engagement": {"score": 1, "num_comments": 1}}
    raw_two = {**raw_one, "title": "two"}
    from lib import normalize
    first = normalize.normalize_source_items("reddit", [{**raw_one, "comment_enrichment_state": "not_enriched"}], "2026-07-01", "2026-07-25", comment_validation="deferred")[0]
    second = normalize.normalize_source_items("reddit", [{**raw_two, "comment_enrichment_state": "not_enriched"}], "2026-07-01", "2026-07-25", comment_validation="deferred")[0]
    assert first.metadata["reddit_post_id"] == ""
    assert pipeline.canonical_reddit_identity(first) != pipeline.canonical_reddit_identity(second)


def test_real_scrapecreators_post_id_is_preserved():
    from lib import normalize
    item = normalize.normalize_source_items("reddit", [{
        "id": "R1", "reddit_id": "t3_1ab2cd", "title": "fixture", "url": "https://reddit.com/r/f/comments/1ab2cd/title/",
        "date": "2026-07-20", "subreddit": "f", "engagement": {"score": 2, "num_comments": 3},
        "comment_enrichment_state": "not_enriched",
    }], "2026-07-01", "2026-07-25", comment_validation="deferred")[0]
    assert item.metadata["reddit_post_id"] == "1ab2cd"
    assert pipeline.canonical_reddit_identity(item) == "reddit-id:1ab2cd"


def test_company_enrichment_is_bounded_and_uses_sc_then_public_fallback(monkeypatch):
    posts = [
        _item(str(index + 100), f"https://reddit.com/r/f/comments/{index + 100}/title/", group="group-alpha", label=f"a-{index}", query="alpha", score=index + 1)
        for index in range(25)
    ]
    sc_calls, public_calls = [], []
    monkeypatch.setattr(pipeline.reddit, "enrich_scrapecreators_post_comments", lambda url, token: sc_calls.append(url) or {"top_comments": []})
    monkeypatch.setattr(pipeline.reddit_keyless, "enrich_public_post_comments", lambda url: public_calls.append(url) or {"top_comments": [{"excerpt": "usable"}]})
    usable, counters = pipeline.enrich_provisional_reddit_items(posts, config={"SCRAPECREATORS_API_KEY": "dummy"}, budget=24, group_order=["group-alpha"])
    assert len(usable) == 24
    assert len(sc_calls) == len(public_calls) == 24
    assert counters["posts_skipped_by_comment_tree_budget"] == 1
    assert counters["public_comment_fallbacks"] == 24
    assert all(item.metadata["comment_enrichment_state"] == "enriched_usable" for item in usable)


def test_enrichment_preserves_provenance_and_excludes_empty(monkeypatch):
    first = _item("same", "https://reddit.com/r/f/comments/same/one/", group="group-alpha", label="a", query="alpha")
    second = _item("same", "https://reddit.com/r/f/comments/same/two/", group="group-beta", label="b", query="beta")
    monkeypatch.setattr(pipeline.reddit_keyless, "enrich_public_post_comments", lambda url: {"top_comments": []})
    usable, counters = pipeline.enrich_provisional_reddit_items([first, second], config={}, budget=24, group_order=["group-alpha", "group-beta"])
    assert usable == []
    assert counters["unique_posts_considered_for_comment_enrichment"] == 1
    assert counters["posts_enriched_empty"] == 1


def test_preliminary_components_are_bounded_and_freshness_uses_100_point_scale():
    item = _item("quality", "https://reddit.com/r/f/comments/quality/title/", group="group-alpha", label="a", query="alpha", score=1000000)
    item.local_relevance = 2.0
    item.freshness = 100
    item.engagement["num_comments"] = 1000000
    components = pipeline.reddit_preliminary_components(item)
    assert components == {"relevance": 1.0, "freshness": 1.0, "comments": 1.0, "score": 1.0}


def test_primary_usable_avoids_fallback_and_empty_primary_is_not_failure(monkeypatch):
    post = _item("usable", "https://reddit.com/r/f/comments/usable/title/", group="group-alpha", label="a", query="alpha")
    monkeypatch.setattr(pipeline.reddit, "enrich_scrapecreators_post_comments", lambda url, token: {"top_comments": [{"excerpt": "yes"}]})
    public = mock.Mock()
    monkeypatch.setattr(pipeline.reddit_keyless, "enrich_public_post_comments", public)
    usable, counters = pipeline.enrich_provisional_reddit_items([post], config={"SCRAPECREATORS_API_KEY": "dummy"}, budget=24, group_order=["group-alpha"])
    assert len(usable) == 1 and counters["scrapecreators_comment_usable_responses"] == 1
    assert counters["public_comment_attempts"] == 0
    assert counters["scrapecreators_comment_failures"] == 0
    public.assert_not_called()


def test_direct_run_scoped_cache_hit_prevents_second_network_call(monkeypatch):
    cache = {}
    calls = []
    monkeypatch.setattr(pipeline.reddit_keyless, "enrich_public_post_comments", lambda url: calls.append(url) or {"top_comments": [{"excerpt": "yes"}]})
    first = _item("cached", "https://reddit.com/r/f/comments/cached/one/", group="group-alpha", label="a", query="alpha")
    second = _item("cached", "https://reddit.com/r/f/comments/cached/two/", group="group-alpha", label="b", query="beta")
    pipeline.enrich_provisional_reddit_items([first], config={}, budget=24, group_order=["group-alpha"], cache=cache)
    _, counters = pipeline.enrich_provisional_reddit_items([second], config={}, budget=24, group_order=["group-alpha"], cache=cache)
    assert len(calls) == 1
    assert counters["comment_tree_cache_hits"] == 1


def test_post_outcomes_partition_selected_posts_and_pending_is_budget_only(monkeypatch):
    posts = [_item(str(index), f"https://reddit.com/r/f/comments/{index}/x/", group="group-alpha", label=str(index), query="alpha") for index in range(3)]
    responses = iter(({"top_comments": [{"excerpt": "yes"}]}, {"top_comments": []}, RuntimeError("down")))
    def public(url):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(pipeline.reddit_keyless, "enrich_public_post_comments", public)
    _, counters = pipeline.enrich_provisional_reddit_items(posts, config={}, budget=2, group_order=["group-alpha"])
    assert counters["posts_enriched_usable"] + counters["posts_enriched_empty"] + counters["posts_enrichment_failed"] == counters["posts_selected_for_comment_enrichment"]
    assert counters["posts_skipped_by_comment_tree_budget"] == 1


def test_group_expansion_runs_once_for_merged_group_and_preserves_group_provenance(monkeypatch):
    base = [
        _item("a", "https://reddit.com/r/UnknownOne/comments/a/x/", group="group-alpha", label="alpha-1", query="first"),
        _item("b", "https://reddit.com/r/UnknownTwo/comments/b/x/", group="group-alpha", label="alpha-2", query="second"),
    ]
    monkeypatch.setattr(pipeline.reddit, "discover_subreddits", lambda posts, **kwargs: ["UnknownOne", "unknownone", "Blocked", "UnknownTwo"])
    monkeypatch.setattr(pipeline.normalize, "_is_excluded_reddit_subreddit", lambda name: str(name).lower() == "blocked")
    calls = []
    monkeypatch.setattr(pipeline.reddit, "search_reddit_in_subreddits", lambda query, subs, **kwargs: calls.append((query, subs)) or [{
        "id": "R1", "reddit_id": "t3_expanded", "title": "expanded", "url": "https://reddit.com/r/unknownone/comments/expanded/x/",
        "date": "2026-07-20", "subreddit": "UnknownOne", "engagement": {"score": 1, "num_comments": 1},
    }])
    representative = schema.SubQuery(label="alpha-1", group_id="group-alpha", search_query="first", ranking_query="first", sources=["reddit"])
    expanded, counters = pipeline.expand_group_reddit_subreddits(group_id="group-alpha", base_items=base, representative=representative, config={"SCRAPECREATORS_API_KEY": "dummy"}, depth="quick", from_date="2026-07-01", to_date="2026-07-25", max_subreddits=3, freshness_mode="strict_recent")
    assert counters["subreddit_discovery_groups_processed"] == 1
    assert calls == [("first", ["UnknownOne"]), ("first", ["UnknownTwo"])]
    assert expanded[0].metadata["group_ids"] == ["group-alpha"]


def test_public_targeted_subreddit_api_uses_only_targeted_discovery(monkeypatch):
    from lib import reddit_keyless
    calls = []
    monkeypatch.setattr(reddit_keyless, "_discover", lambda topic, depth, subreddits, dedicated_subreddits=None, base_only=False: calls.append((subreddits, base_only)) or [{"url": "https://reddit.com/r/fictional/comments/a/x/", "date": "2026-07-20", "engagement": {"score": 1, "num_comments": 1}}])
    posts = reddit_keyless.search_public_in_subreddits("group ranking query", ["fictional"], from_date="2026-07-01", to_date="2026-07-25")
    assert calls == [(["fictional"], True)]
    assert posts[0]["comment_enrichment_state"] == "not_enriched"


def test_group_expansion_falls_back_only_for_empty_or_failed_subreddit(monkeypatch):
    base = [_item("a", "https://reddit.com/r/one/comments/a/x/", group="group-alpha", label="a", query="first")]
    monkeypatch.setattr(pipeline.reddit, "discover_subreddits", lambda *args, **kwargs: ["one", "two", "three"])
    def primary(query, subs, **kwargs):
        if subs == ["one"]:
            return [{"id": "R1", "reddit_id": "oneid", "title": "one", "url": "https://reddit.com/r/one/comments/oneid/x/", "date": "2026-07-20", "subreddit": "one", "engagement": {"score": 1, "num_comments": 1}}]
        if subs == ["two"]:
            return []
        raise RuntimeError("down")
    public_calls = []
    monkeypatch.setattr(pipeline.reddit, "search_reddit_in_subreddits", primary)
    monkeypatch.setattr(pipeline.reddit_keyless, "search_public_in_subreddits", lambda query, subs, **kwargs: public_calls.append(subs) or [])
    sq = schema.SubQuery(label="a", group_id="group-alpha", search_query="first", ranking_query="stable group query", sources=["reddit"])
    _, counters = pipeline.expand_group_reddit_subreddits(group_id="group-alpha", base_items=base, representative=sq, config={"SCRAPECREATORS_API_KEY": "dummy"}, depth="quick", from_date="2026-07-01", to_date="2026-07-25", max_subreddits=3, freshness_mode="strict_recent")
    assert public_calls == [["two"], ["three"]]
    assert counters["subreddit_expansion_primary_usable"] == 1
    assert counters["subreddit_expansion_primary_empty"] == 1
    assert counters["subreddit_expansion_primary_failed"] == 1
    assert counters["subreddit_expansion_public_fallbacks"] == 2


def test_base_search_diagnostics_reflect_actual_primary_and_fallback_calls(monkeypatch):
    sq = schema.SubQuery(label="alpha", group_id="group-alpha", search_query="fictional", ranking_query="fictional", sources=["reddit"])
    monkeypatch.setattr(pipeline.reddit, "search_and_enrich", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(pipeline.reddit, "parse_reddit_response", lambda result: [])
    monkeypatch.setattr(pipeline.reddit_public, "search_reddit_public", lambda *args, **kwargs: [{"id": "R1"}])
    items, artifact = pipeline._retrieve_reddit_stream(topic="fictional", subquery=sq, config={"SCRAPECREATORS_API_KEY": "dummy", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators", "_reddit_search_execution_mode": "group_scoped_subreddit_expansion"}, depth="quick", from_date="2026-07-01", to_date="2026-07-25", raw_topic="fictional", subreddits=None)
    diag = artifact["reddit_search_diagnostics"]
    assert len(items) == 1
    assert diag["primary_search_attempts"] == 1
    assert diag["primary_search_empty"] == 1
    assert diag["public_fallback_invocations"] == 1
    assert diag["accepted_record_count"] == 1
