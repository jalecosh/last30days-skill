"""Focused company entity discovery contracts, using only mocked Yahoo responses."""

from __future__ import annotations

from datetime import date
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("research_company_entity_tests", ROOT / "tools" / "research_company.py")
assert SPEC and SPEC.loader
research_company = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = research_company
SPEC.loader.exec_module(research_company)


def _autodesk() -> object:
    return research_company.CompanyIdentity("ADSK", "Autodesk, Inc.", "Autodesk")


def test_legal_suffixes_normalize_short_names():
    assert research_company.normalize_short_name("Autodesk, Inc.") == "Autodesk"
    assert research_company.normalize_short_name("Adobe Inc.") == "Adobe"


def test_unrelated_dwave_corporation_is_rejected_for_autodesk(monkeypatch):
    monkeypatch.setattr(research_company, "_yahoo", lambda query: {"quotes": [{"quoteType": "EQUITY", "shortname": "D-Wave Quantum Inc."}]})
    assert research_company.discover_company_entities(_autodesk()) == ["Autodesk", "Autodesk, Inc."]


def test_unrelated_corporate_legal_names_are_rejected_generically():
    assert not research_company.is_valid_company_entity("Sony Group Corporation", _autodesk())


def test_explicitly_associated_product_is_accepted(monkeypatch):
    product = {"quoteType": "EQUITY", "shortname": "AutoCAD", "description": "Autodesk AutoCAD design software"}
    monkeypatch.setattr(research_company, "_yahoo", lambda query: {"quotes": [product]})
    assert "AutoCAD" in research_company.discover_company_entities(_autodesk())


def test_no_valid_products_uses_company_name_only_groups():
    groups = research_company._generic_groups(_autodesk())
    queries = [query for group in groups for query in group["queries"]]
    assert len(groups) == 5
    assert len(queries) == 27
    assert all(query.startswith("Autodesk") for query in queries)


def test_query_generation_never_uses_rejected_entity():
    groups = research_company._generic_groups(_autodesk())
    assert all("D-Wave" not in query for group in groups for query in group["queries"])


def test_mocked_adsk_plan_is_autodesk_only(monkeypatch):
    def yahoo(query):
        if query == "ADSK":
            return {"quotes": [{"symbol": "ADSK", "quoteType": "EQUITY", "longname": "Autodesk, Inc.", "shortname": "Autodesk, Inc.", "exchange": "NMS", "industry": "Software"}]}
        return {"quotes": [{"quoteType": "EQUITY", "shortname": "D-Wave Quantum Inc."}]}

    monkeypatch.setattr(research_company, "_yahoo", yahoo)
    resolved = research_company.resolve_run(research_company.normalize_ticker("ADSK"))
    plan = research_company.complete_plan(research_company.normalize_ticker("ADSK"), resolved, date(2026, 7, 27), 30)
    queries = [query["query"] for query in plan["queries"]]
    assert plan["resolved_short_name"] == "Autodesk"
    assert any(query.startswith("Autodesk") for query in queries)
    assert not any("D-Wave" in query for query in queries)
    assert "ADSK" not in queries