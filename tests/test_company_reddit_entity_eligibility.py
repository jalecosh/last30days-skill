"""Focused title-only eligibility tests for company Reddit candidates."""

from lib import pipeline


ENTITIES = [
    "Autodesk", "Autodesk, Inc.", "AutoCAD", "Revit", "Fusion 360",
    "Maya", "3ds Max", "Autodesk Forma",
]


def matches(title="", body=""):
    return pipeline.matches_company_entity(title, body, ENTITIES)


def test_autodesk_in_title_passes():
    assert matches("Autodesk audit", "")


def test_case_insensitive_autodesk_in_title_passes():
    assert matches("AUTODESK DEPLOYMENT", "")


def test_validated_product_entity_in_title_passes():
    assert matches("Is an AutoCAD drafting career stable?", "")


def test_autodesk_once_in_body_fails():
    assert not matches("Licensing question", "Our Autodesk seats need renewal.")


def test_autodesk_multiple_times_in_body_still_fails():
    assert not matches("Licensing question", "Autodesk seats need renewal. Autodesk support was unhelpful.")


def test_autodesk_only_in_comments_fails():
    fixture = {"title": "IBM dropped 24%", "selftext": "AI infrastructure capex", "comments": ["Autodesk comparison"]}
    assert not matches(fixture["title"], fixture["selftext"])


def test_ibm_title_fails():
    assert not matches("IBM dropped 24%", "Autodesk appears twice. Autodesk appears again.")


def test_adobe_title_fails():
    assert not matches("Adobe's AI Bet Still Has to Prove the Economics", "Autodesk differs from Adobe. Autodesk has another model.")


def test_generic_solidworks_title_fails():
    assert not matches("Oh no, are we at that point now?", "Autodesk is mentioned. Autodesk is mentioned again.")


def test_generic_subscription_title_fails():
    assert not matches("Tired of software subscriptions", "Autodesk is expensive. Autodesk is still expensive.")


def test_generic_ai_title_fails():
    assert not matches("Found this AI masterpiece on another subreddit", "Autodesk appears here. Autodesk appears again.")


def test_generic_entity_is_excluded_from_title_matching():
    assert not pipeline.matches_company_entity("CAD workflow", "", ["CAD"])


def test_overlapping_legal_and_short_name_title_match_once():
    assert matches("Autodesk, Inc. licensing update", "")


def test_candidate_count_can_shrink_without_backfill():
    fixtures = [
        ("Autodesk piracy", ""),
        ("IBM dropped 24%", "Autodesk comparison"),
        ("Autodesk Forma Web Viewer", "IFC files"),
        ("Generic AI career switch", "Autodesk mentioned twice. Autodesk mentioned again."),
    ]
    accepted = [fixture for fixture in fixtures if matches(*fixture)]
    assert accepted == [fixtures[0], fixtures[2]]


PTC_ENTITIES = ["PTC", "PTC Inc.", "Parametric Technology", "Creo", "Windchill"]


def ptc_matches(title="", body=""):
    entities = pipeline.company_title_eligibility_entities(PTC_ENTITIES, "PTC")
    return pipeline.matches_company_entity(title, body, entities)


def test_exact_bare_ticker_is_not_a_company_title_eligibility_entity():
    assert pipeline.company_title_eligibility_entities(PTC_ENTITIES, "PTC") == [
        "PTC Inc.", "Parametric Technology", "Creo", "Windchill",
    ]
    assert not ptc_matches("PTC earnings outlook", "")


def test_unrelated_ptc_titles_fail_without_a_non_ticker_entity():
    for title in (
        "PTC Study Group",
        "Pokemon GO PTC account",
        "PTC India Finance",
        "PTC internal position update",
    ):
        assert not ptc_matches(title, "")


def test_ptc_full_name_and_discovered_product_titles_pass():
    assert ptc_matches("PTC Inc. earnings outlook", "")
    assert ptc_matches("Windchill deployment outlook", "")
    assert ptc_matches("Creo subscription pricing", "")


def test_all_rejected_company_candidates_stay_empty():
    candidates = [
        ("PTC Study Group", ""),
        ("Pokemon GO PTC account", ""),
        ("PTC India Finance", ""),
    ]
    assert [candidate for candidate in candidates if ptc_matches(*candidate)] == []


def test_adsk_and_adbe_company_title_eligibility_remain_valid():
    assert pipeline.matches_company_entity(
        "Autodesk earnings", "",
        pipeline.company_title_eligibility_entities(["ADSK", "Autodesk"], "ADSK"),
    )
    assert pipeline.matches_company_entity(
        "Adobe valuation", "",
        pipeline.company_title_eligibility_entities(["ADBE", "Adobe"], "ADBE"),
    )