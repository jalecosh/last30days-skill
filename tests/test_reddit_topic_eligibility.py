import unittest
from unittest.mock import patch

from lib import normalize, pipeline, reddit_topic_eligibility as eligibility, schema, signals


def item(title, body="", *, subreddit="unknown", comments=None):
    return schema.SourceItem(
        item_id="post", source="reddit", title=title, body=body, url="https://reddit.com/r/test/comments/abc/post/",
        container=subreddit, engagement={"score": 10, "num_comments": 2},
        metadata={"reddit_post_body": body, "comment_tree": comments or []},
    )


def evaluate(title, body="", *, subreddit="unknown", comments=None):
    return eligibility.evaluate(
        item(title, body, subreddit=subreddit, comments=comments),
        subquery_label="creative-products-workflows",
        search_query="Adobe Creative Cloud Photoshop workflow",
        ranking_query="Adobe workflows and customer experience",
    )


class AdobeTopicEligibilityTests(unittest.TestCase):
    def test_direct_adobe_valuation_passes(self):
        result = evaluate("Adobe valuation after earnings", "ADBE cash flow, buybacks, and AI margins")
        self.assertTrue(result.eligible)
        self.assertTrue(result.title_entity_match)

    def test_adobe_cancellation_customer_complaint_passes(self):
        result = evaluate("Adobe cancellation fee is out of control", "Creative Cloud subscription customer complaint")
        self.assertTrue(result.eligible)

    def test_photoshop_replacement_workflow_passes(self):
        result = evaluate("Photoshop replacement for a professional workflow", "Need an alternative for client design work")
        self.assertTrue(result.eligible)

    def test_adobe_firefly_pricing_passes(self):
        result = evaluate("Adobe Firefly credits and pricing", "Adobe AI credits are too expensive")
        self.assertTrue(result.eligible)

    def test_ambiguous_or_incidental_mentions_fail(self):
        cases = [
            ("Firefly sightings in my garden", "A firefly landed on my camera", "nature", []),
            ("Pokemon event discussion", "Great event tonight", "pokemongo", [{"author": "u", "body": "I use Adobe to edit screenshots"}]),
            ("Piracy software list", "Adobe, Office, games, and many other software apps are mentioned here", "PiracyBackup", []),
            ("My favorite AI tools", "Midjourney, Firefly, Gemini, and many other tools", "isthisAI", []),
        ]
        for title, body, subreddit, comments in cases:
            with self.subTest(title=title):
                result = evaluate(title, body, subreddit=subreddit, comments=comments)
                self.assertFalse(result.eligible)
                self.assertTrue(result.rejection_reasons)

    def test_generic_title_can_pass_with_adobe_centered_body_and_comments(self):
        result = evaluate(
            "Need advice before renewal",
            "Adobe Creative Cloud is central to my work. Adobe pricing and cancellation terms changed again.",
            comments=[
                {"author": "a", "body": "Adobe subscription costs are hard to justify."},
                {"author": "b", "body": "Creative Cloud workflow migration is expensive."},
            ],
        )
        self.assertTrue(result.eligible)
        self.assertGreaterEqual(result.relevant_comment_count, 2)

    def test_subreddit_is_never_positive_eligibility_evidence(self):
        unknown = evaluate("Adobe workflow migration", "Photoshop and Illustrator replacement", subreddit="creativeworkflow")
        adobe_unrelated = evaluate("Weekend plans", "Nothing about software", subreddit="Adobe")
        valueinvesting_unrelated = evaluate("Market meme", "No company analysis here", subreddit="ValueInvesting")
        self.assertTrue(unknown.eligible)
        self.assertFalse(adobe_unrelated.eligible)
        self.assertFalse(valueinvesting_unrelated.eligible)

    def test_recent_report_style_subreddit_categories_are_content_judged(self):
        for subreddit in ("pokemongo", "Indian_flex", "BestofRedditorUpdates", "PiracyBackup", "Superstonk"):
            with self.subTest(subreddit=subreddit):
                result = evaluate(
                    "Unrelated community thread", "General discussion with no Adobe product focus",
                    subreddit=subreddit,
                    comments=[{"author": "u", "body": "Adobe appears once in a side note"}],
                )
                self.assertFalse(result.eligible)
        self.assertTrue(evaluate(
            "Adobe earnings and cash flow", "ADBE buybacks and Firefly economics", subreddit="Superstonk",
        ).eligible)

    def test_explainability_metadata_is_preserved(self):
        source = item("Adobe earnings", "ADBE valuation and cash flow")
        result = eligibility.evaluate(source, subquery_label="investment", search_query="Adobe earnings", ranking_query="Adobe valuation")
        self.assertTrue(result.eligible)
        for key in (
            "topic_eligible", "topic_eligibility_score", "topic_positive_signals",
            "topic_rejection_reasons", "title_entity_match", "body_entity_match",
            "relevant_comment_count", "usable_comment_count", "matched_subquery_label",
        ):
            self.assertIn(key, source.metadata)

    def test_existing_global_subreddit_weighting_and_base_ranking_are_unchanged(self):
        original = normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS, normalize.PREFERRED_SUBREDDITS
        normalize.BLOCKED_SUBREDDITS = frozenset({"bannedtestforum"})
        normalize.EXCLUDED_SUBREDDITS = normalize.BLOCKED_SUBREDDITS
        normalize.PREFERRED_SUBREDDITS = {"preferredtestforum"}
        try:
            self.assertEqual(1.5, normalize.reddit_subreddit_quality_multiplier("r/PreferredTestForum"))
            self.assertEqual(1.00, normalize.reddit_subreddit_quality_multiplier("r/UnlistedTestForum"))
            self.assertEqual(0.0, normalize.reddit_subreddit_quality_multiplier("r/BannedTestForum"))
            self.assertEqual(0.35, signals.reddit_rank_score(1.0, 0.0, 0.0, 0.0))
            self.assertEqual(0.25, signals.reddit_rank_score(0.0, 1.0, 0.0, 0.0))
            self.assertEqual(0.25, signals.reddit_rank_score(0.0, 0.0, 1.0, 0.0))
            self.assertEqual(0.15, signals.reddit_rank_score(0.0, 0.0, 0.0, 1.0))
        finally:
            normalize.BLOCKED_SUBREDDITS, normalize.EXCLUDED_SUBREDDITS, normalize.PREFERRED_SUBREDDITS = original


class AdobeTopicEligibilityRelevanceTests(unittest.TestCase):
    def test_indian_flex_style_post_fails_without_title_or_original_body_evidence(self):
        result = evaluate(
            "It took 260hrs to do this solo + lots of coffee fueled it. No AI",
            "A personal animation showcase with no product discussion.",
            subreddit="Indian_flex",
            comments=[{"author": "u", "body": "Adobe Firefly is interesting."}],
        )
        self.assertFalse(result.eligible)
        self.assertIn("comment_only_or_incidental_mention", result.rejection_reasons)

    def test_search_and_subquery_metadata_cannot_make_a_post_eligible(self):
        source = item("No AI", "A generic artwork post", comments=[])
        result = eligibility.evaluate(
            source,
            subquery_label="adobe-firefly-economics",
            search_query="Adobe Firefly pricing Creative Cloud cancellation",
            ranking_query="Adobe valuation earnings workflow",
        )
        self.assertFalse(result.eligible)
        self.assertGreaterEqual(result.matched_subquery_support, 0.0)

    def test_comment_only_adobe_reference_cannot_rescue_unrelated_post(self):
        result = evaluate(
            "Pokemon event discussion", "Raid details and community plans.",
            comments=[{"author": "u", "body": "I use Adobe Photoshop for screenshots."}],
        )
        self.assertFalse(result.eligible)

    def test_generic_title_with_repeated_adobe_business_body_passes(self):
        result = evaluate(
            "Is this sustainable?",
            "ADBE valuation depends on Adobe cash flow and Adobe buybacks. Adobe AI compute costs may pressure margins.",
        )
        self.assertTrue(result.eligible)
        self.assertTrue(result.body_entity_match)
        self.assertTrue(result.original_body_evidence)

    def test_relevance_metadata_and_low_value_ordering(self):
        substantive = item(
            "Adobe valuation after earnings",
            "ADBE revenue, margins, free cash flow, buybacks, Firefly compute costs, and customer retention determine valuation.",
        )
        piracy = item(
            "How do I pirate Adobe Photoshop?",
            "Need a download crack and activation instructions for Photoshop.",
        )
        installation = item(
            "Adobe installation problem",
            "How do I install this application?",
        )
        workflow = item(
            "Leaving Adobe after renewal",
            "Creative Cloud pricing and cancellation policy forced a professional workflow migration. Switching costs and replacement options are substantial.",
        )
        for source in (substantive, piracy, installation, workflow):
            result = eligibility.evaluate(source, subquery_label="adobe", search_query="Adobe workflow", ranking_query="Adobe valuation workflow")
            self.assertTrue(result.eligible)
            source.local_relevance = 0.8
            source.freshness = 80
            source.metadata["reddit_normalized_comments"] = 0.2
            source.metadata["reddit_normalized_post_score"] = 0.2
            signals.apply_adobe_reddit_relevance(source)
            for key in ("topic_centrality_score", "research_value_score", "final_relevance_score"):
                self.assertIn(key, source.metadata)
        self.assertGreater(substantive.metadata["research_value_score"], piracy.metadata["research_value_score"])
        self.assertGreater(workflow.metadata["research_value_score"], installation.metadata["research_value_score"])
        self.assertGreater(substantive.metadata["final_relevance_score"], piracy.metadata["final_relevance_score"])


class AdobeTopicGatePipelineTests(unittest.TestCase):
    def test_pipeline_gates_after_normalization_before_fusion_and_counts_rejections(self):
        plan = {
            "intent": "opinion", "freshness_mode": "balanced_recent", "cluster_mode": "debate",
            "subqueries": [{
                "label": "creative", "search_query": "Adobe Photoshop workflow",
                "ranking_query": "Adobe workflows", "sources": ["reddit"], "weight": 1.0,
            }],
            "source_weights": {"reddit": 1.0},
        }
        raw = [
            {
                "id": "good", "title": "Adobe Photoshop workflow replacement", "selftext": "Adobe Creative Cloud pricing is central",
                "url": "https://reddit.com/r/creativeworkflow/comments/good/post/", "date": "2026-07-20", "subreddit": "creativeworkflow",
                "relevance": 0.95, "engagement": {"score": 20, "num_comments": 4},
                "top_comments": [{"author": "a", "excerpt": "Adobe workflow", "score": 2}],
            },
            {
                "id": "pokemon", "title": "Pokemon event", "selftext": "Community raid details",
                "url": "https://reddit.com/r/pokemongo/comments/bad/post/", "date": "2026-07-20", "subreddit": "pokemongo",
                "relevance": 0.95, "engagement": {"score": 20, "num_comments": 4},
                "top_comments": [{"author": "a", "excerpt": "Adobe is useful for screenshots", "score": 2}],
            },
        ]
        with patch("lib.pipeline._retrieve_stream", return_value=(raw, {})), \
             patch("lib.pipeline._retry_thin_sources"), \
             patch("lib.pipeline._run_supplemental_searches"), \
             patch("lib.pipeline._load_library_context", return_value=(None, None)):
            report = pipeline.run(
                topic="Adobe", config={"LAST30DAYS_REASONING_PROVIDER": "gemini"}, depth="default",
                requested_sources=["reddit"], mock=True, external_plan=plan,
            )
        self.assertEqual(["good"], [entry.item_id for entry in report.items_by_source["reddit"]])
        counters = report.artifacts["reddit_recall"]
        self.assertTrue(counters["topic_gate_applied"])
        self.assertEqual(2, counters["candidates_evaluated_by_topic_gate"])
        self.assertEqual(1, counters["candidates_accepted_by_topic_gate"])
        self.assertEqual(1, counters["candidates_rejected_by_topic_gate"])
        self.assertEqual({"creative": 1}, counters["accepted_count_per_subquery"])
        self.assertEqual({"creativeworkflow": 1}, counters["accepted_count_per_subreddit"])
        self.assertEqual(1, len(report.artifacts["reddit_topic_gate_rejections"]))
        candidate = report.ranked_candidates[0]
        self.assertTrue(candidate.metadata["topic_eligible"])
        self.assertEqual("creative", candidate.metadata["matched_subquery_label"])

    def test_non_adobe_reddit_plan_is_unchanged_by_gate(self):
        plan = schema.QueryPlan(
            intent="opinion", freshness_mode="balanced_recent", cluster_mode="debate", raw_topic="Other",
            subqueries=[schema.SubQuery(label="other", search_query="Other topic", ranking_query="Other topic", sources=["reddit"])],
            source_weights={"reddit": 1.0},
        )
        self.assertFalse(pipeline._should_apply_adobe_reddit_topic_gate("Other", plan))


if __name__ == "__main__":
    unittest.main()
