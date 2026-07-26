import unittest

from lib import health, render, schema


_POST_URL = "https://reddit.com/r/LocalLLaMA/comments/1/post/"


def reddit_item(
    title="Post title",
    subreddit="LocalLLaMA",
    published_at="2026-06-30T12:34:56+00:00",
    url=_POST_URL,
    comments=None,
):
    return schema.SourceItem(
        item_id="reddit-item",
        source="reddit",
        title=title,
        body="Reddit discussion body.",
        url=url,
        published_at=published_at,
        container=subreddit,
        author="poster",
        engagement={"score": 5, "num_comments": 2},
        metadata={"comment_tree": comments or []},
    )


def reddit_only_report(*, items=None, warnings=None, source_status=None, errors=None):
    items = items or [reddit_item(comments=[{"author": "root", "body": "usable", "score": 1}])]
    primary = items[0]
    candidate = schema.Candidate(
        candidate_id="reddit-1", item_id=primary.item_id, source="reddit",
        title=primary.title, url=primary.url, snippet=primary.body,
        subquery_labels=[], native_ranks={}, local_relevance=0.9, freshness=90,
        engagement=8, source_quality=0.6, rrf_score=0.02, source_items=[primary],
    )
    return schema.Report(
        topic="Adobe",
        range_from="2026-06-26",
        range_to="2026-07-25",
        generated_at="2026-07-25T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(reasoning_provider="test", planner_model="test", rerank_model="test"),
        query_plan=schema.QueryPlan(
            intent="product", freshness_mode="balanced_recent", cluster_mode="story",
            raw_topic="Adobe", subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[candidate], items_by_source={"reddit": items},
        errors_by_source=errors or {},
        source_status=source_status or {"reddit": schema.SourceOutcome("reddit", health.OK, len(items))},
        warnings=warnings or [],
        artifacts={"requested_sources": ["reddit"]},
    )


class RedditRawMarkdownRenderingTests(unittest.TestCase):
    def test_header_is_language_neutral_and_preserves_title_subreddit_date_and_url(self):
        item = reddit_item(title="A [title]_raw", subreddit="r/graphic_design", comments=[{"author": "a", "body": "b", "score": 1}])
        header = render._render_reddit_discussion(item)[0]

        self.assertEqual(
            header,
            "## A [title]_raw\u3000\u3000r/graphic_design\u3000\u30002026-06-30\u3000\u3000[URL](https://reddit.com/r/LocalLLaMA/comments/1/post/)",
        )
        for label in ("\u7248\u5757\uff1a", "\u539f\u5e16\u94fe\u63a5", "User", "Original post"):
            self.assertNotIn(label, header)

    def test_blank_subreddit_and_duplicate_prefix_have_safe_raw_references(self):
        blank = render._render_reddit_discussion(reddit_item(subreddit="", comments=[{"body": "usable"}]))[0]
        duplicate = render._render_reddit_discussion(reddit_item(subreddit="R/ValueInvesting", comments=[{"body": "usable"}]))[0]

        self.assertIn("r/unknown", blank)
        self.assertNotIn("\u3000\u3000r/\u3000\u3000", blank)
        self.assertIn("r/ValueInvesting", duplicate)

    def test_comment_tree_is_language_neutral_source_preserving_and_has_numeric_scores(self):
        tree = [{
            "author": "narayan77", "body": "root [body]_raw", "score": 132,
            "date": "2026-07-13 14:40", "replies": [{
                "author": "dopexile", "parent_author": "narayan77", "body": "reply", "score": 70,
                "date": "2026-07-13 15:58", "replies": [{
                    "author": "Few_Economics_8176", "parent_author": "dopexile", "body": "nested", "score": 15,
                    "date": "2026-07-13 18:20",
                }],
            }],
        }]
        text = "\n".join(render._render_reddit_discussion(reddit_item(comments=tree)))

        self.assertIn("narayan77\uff1aroot [body]_raw\u3000\u3000\u3000132", text)
        self.assertIn("\u3000\u21b3 dopexile \u2192 narayan77\uff1areply\u3000\u3000\u300070", text)
        self.assertIn("\u3000\u3000\u21b3 Few_Economics_8176 \u2192 dopexile\uff1anested\u3000\u3000\u300015", text)
        self.assertNotIn("Few\\_Economics", text)
        self.assertNotIn("2026-07-13 14:40", text)
        self.assertFalse(any(line.startswith("    ") for line in text.splitlines()))
        for label in ("\u7528\u6237", "\u56de\u590d", "\u8d5e", "User", "replying to", "upvotes"):
            self.assertNotIn(label, text)

    def test_commentless_item_has_no_presentation_placeholder(self):
        text = "\n".join(render._render_reddit_discussion(reddit_item(comments=[])))
        self.assertNotIn("No usable comments", text)
        self.assertNotIn("\u6682\u65e0\u53ef\u7528\u8bc4\u8bba", text)

    def test_three_blank_lines_separate_reddit_posts(self):
        first = reddit_item(title="First", comments=[{"body": "one", "score": 1}])
        second = reddit_item(title="Second", comments=[{"body": "two", "score": 2}])
        text = "\n".join(render._render_reddit_discussions([first, second]))
        self.assertIn("1\n\n\n\n## Second", text)
        self.assertNotIn("1\n\n\n\n\n## Second", text)

    def test_diagnostics_have_exactly_three_blank_lines_before_first_post(self):
        report = reddit_only_report(
            warnings=["A real warning"],
            source_status={"reddit": schema.SourceOutcome("reddit", health.OK, 1)},
        )
        text = render.render_full(report)
        first_header = render._render_reddit_discussion(report.items_by_source["reddit"][0])[0]
        final_diagnostic_line = "- Reddit: 1 item"
        self.assertIn(final_diagnostic_line + "\n\n\n\n" + first_header, text)
        self.assertNotIn(final_diagnostic_line + "\n\n\n\n\n" + first_header, text)

    def test_reddit_only_report_keeps_raw_english_diagnostics(self):
        detail = "Connection error: TimeoutError: read operation timed out"
        report = reddit_only_report(
            warnings=["Library context unavailable: [WinError 5] Access is denied: 'C:\\Users\\jalec\\.local\\share\\last30days\\library.db'"],
            source_status={
                "reddit": schema.SourceOutcome("reddit", health.OK, 1),
                "jobs": schema.SourceOutcome("jobs", health.TIMEOUT, 0, detail=detail, fix_hint="doctor"),
            },
            errors={"jobs": detail},
        )
        text = render.render_full(report)

        for heading in ("## Freshness", "## Warnings", "## Partial Coverage", "## Source Coverage", "## Source Errors"):
            self.assertIn(heading, text)
        self.assertIn("Library context unavailable: [WinError 5] Access is denied: 'C:\\Users\\jalec\\.local\\share\\last30days\\library.db'", text)
        self.assertIn("TimeoutError", text)
        self.assertIn("run `doctor` for fix prescriptions", text)
        self.assertNotIn("## \u65b0\u9c9c\u5ea6", text)
        self.assertNotIn("## \u8b66\u544a", text)

    def test_empty_diagnostics_are_omitted_and_jobs_are_not_invented(self):
        items = [reddit_item(title=f"Post {index}", published_at="2026-07-24", comments=[{"body": "usable"}]) for index in range(3)]
        text = render.render_full(reddit_only_report(items=items))
        self.assertIn("## Source Coverage", text)
        for heading in ("## Freshness", "## Warnings", "## Partial Coverage", "## Source Errors"):
            self.assertNotIn(heading, text)
        self.assertNotIn("Jobs", text)

    def test_model_boilerplate_is_absent_from_reader_report(self):
        text = render.render_compact(reddit_only_report())
        for boilerplate in ("Safety note", "EVIDENCE FOR SYNTHESIS", "PASS-THROUGH FOOTER", "LAW ", "END OF last30days CANONICAL OUTPUT", "Ranked Evidence Clusters"):
            self.assertNotIn(boilerplate, text)

    def test_non_reddit_candidate_rendering_is_unchanged(self):
        item = schema.SourceItem(item_id="x-item", source="x", title="X post", body="X body", url="https://x.example/post", published_at="2026-06-30", author="author", engagement={"likes": 8})
        candidate = schema.Candidate(candidate_id="x-1", item_id="x-item", source="x", title="X post", url=item.url, snippet="X body", subquery_labels=[], native_ranks={}, local_relevance=0.5, freshness=80, engagement=8, source_quality=1.0, rrf_score=0.01, source_items=[item])
        text = "\n".join(render._render_candidate(candidate, "1."))
        self.assertIn("1. [x] X post", text)
        self.assertIn("https://x.example/post", text)


if __name__ == "__main__":
    unittest.main()
