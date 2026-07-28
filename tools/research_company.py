#!/usr/bin/env python3
"""Ticker-first, broad-discovery Reddit research runner for InvestmentBrain."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = REPO_ROOT / "config" / "research-defaults.json"
ENGINE_PATH = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"
BLOCKLIST_PATH = REPO_ROOT / "config" / "reddit_blocklist.json"
PREFERENCES_PATH = REPO_ROOT / "config" / "reddit" / "preferences.json"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
LEGAL_SUFFIX_RE = re.compile(
    r"(?:,?\s+)(?:inc\.?|incorporated|corporation|corp\.?|ltd\.?|limited|plc|holdings|group|company)\s*$",
    re.IGNORECASE,
)

sys.path.insert(0, str(ENGINE_PATH.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import reddit_policy  # noqa: E402
from lib import pipeline as engine_pipeline  # noqa: E402
from lib import reddit as engine_reddit  # noqa: E402
from query_scheduler import schedule_group_queries  # noqa: E402


@dataclass(frozen=True)
class TickerIdentifier:
    ticker: str
    exchange_qualifier: str | None = None

    @property
    def display(self) -> str:
        return f"{self.exchange_qualifier}:{self.ticker}" if self.exchange_qualifier else self.ticker


@dataclass(frozen=True)
class CompanyIdentity:
    ticker: str
    company_name: str
    short_name: str
    exchange: str | None = None
    industry: str | None = None
    company_website: str | None = None


def normalize_short_name(name: str) -> str:
    """Remove a trailing corporate legal suffix for user-facing company queries."""
    return LEGAL_SUFFIX_RE.sub("", re.sub(r"\s+", " ", name).strip()).strip(" ,")


def _same_company_name(candidate: str, identity: CompanyIdentity) -> bool:
    return normalize_short_name(candidate).casefold() == identity.short_name.casefold()


def _looks_like_corporate_legal_name(candidate: str) -> bool:
    return bool(LEGAL_SUFFIX_RE.search(candidate))


def _source_explicitly_associates_candidate(
    candidate: str, identity: CompanyIdentity, source: dict[str, Any],
) -> bool:
    text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "text", "description", "summary", "longname", "shortname")
    ).casefold()
    return candidate.casefold() in text and identity.short_name.casefold() in text


def is_valid_company_entity(candidate: str, identity: CompanyIdentity, source: dict[str, Any] | None = None) -> bool:
    """Accept only target-owned or explicitly target-associated entity candidates."""
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        return False
    if _same_company_name(candidate, identity):
        return True
    if _looks_like_corporate_legal_name(candidate):
        return False
    if identity.short_name.casefold() in candidate.casefold():
        return True
    return source is not None and _source_explicitly_associates_candidate(candidate, identity, source)


def _yahoo(query: str) -> dict[str, Any]:
    request = Request("https://query1.finance.yahoo.com/v1/finance/search?q=" + quote(query) + "&quotesCount=20&newsCount=0", headers={"User-Agent": "last30days-company-research/1.0"})
    with urlopen(request, timeout=10) as response:  # nosec B310: fixed public endpoint
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("ticker provider returned invalid JSON")
    return value


def resolve_ticker(ticker: str) -> CompanyIdentity:
    """Resolve a ticker with Yahoo Finance's public quote-search endpoint."""
    quotes = _yahoo(ticker).get("quotes", [])
    match = next((q for q in quotes if isinstance(q, dict) and str(q.get("symbol", "")).upper() == ticker.upper() and q.get("quoteType") == "EQUITY"), None)
    if not match:
        raise ValueError(f"could not resolve ticker {ticker} from Yahoo Finance")
    name = str(match.get("longname") or match.get("shortname") or "").strip()
    short = normalize_short_name(str(match.get("shortname") or name).strip())
    if not name or not short:
        raise ValueError(f"Yahoo Finance returned no company name for {ticker}")
    return CompanyIdentity(ticker.upper(), name, short, str(match.get("exchange") or "").strip() or None, str(match.get("industry") or match.get("sector") or "").strip() or None, str(match.get("website") or "").strip() or None)


def discover_company_entities(identity: CompanyIdentity) -> list[str]:
    """Discover only company-associated labels from one generic Yahoo name search."""
    values = [identity.short_name, identity.company_name]
    for quote_item in _yahoo(identity.company_name).get("quotes", []):
        if isinstance(quote_item, dict) and quote_item.get("quoteType") == "EQUITY":
            candidate = str(quote_item.get("shortname") or quote_item.get("longname") or "")
            if is_valid_company_entity(candidate, identity, quote_item):
                values.append(candidate)
    seen: set[str] = set(); result: list[str] = []
    for value in values:
        value = re.sub(r"\s+", " ", value.strip())
        if value and value.casefold() not in seen:
            seen.add(value.casefold()); result.append(value)
    return result


def _generic_groups(identity: CompanyIdentity) -> list[dict[str, Any]]:
    company = identity.short_name
    templates = [
        ("investment_and_valuation", [
            f"{company} stock", f"{company} valuation", f"{company} earnings",
            f"{company} free cash flow", f"{company} buy sell hold", f"{company} bull case bear case",
        ]),
        ("business_performance", [
            f"{company} revenue growth", f"{company} margins", f"{company} pricing power",
            f"{company} subscription growth", f"{company} customer growth", f"{company} business model",
        ]),
        ("management_and_internal", [
            f"{company} management", f"{company} layoffs", f"{company} employees",
            f"{company} company culture", f"{company} acquisition", f"{company} capital allocation",
        ]),
        ("competition_and_future", [
            f"{company} moat", f"{company} competition", f"{company} market share",
            f"{company} switching costs", f"{company} AI risk", f"{company} future growth",
        ]),
        ("customer_and_product_evidence", [
            f"{company} customer complaints", f"{company} alternatives", f"{company} workflow",
        ]),
    ]
    return [{"id": group_id, "ranking_query": queries[0], "queries": list(dict.fromkeys(queries))} for group_id, queries in templates]
def normalize_ticker(raw: str) -> TickerIdentifier:
    """Normalize SYMBOL or the future-compatible EXCHANGE:SYMBOL form."""
    value = raw.strip().upper()
    parts = value.split(":")
    if len(parts) == 1:
        exchange, ticker = None, parts[0]
    elif len(parts) == 2 and all(parts):
        exchange, ticker = parts
    else:
        raise ValueError("ticker must be SYMBOL or EXCHANGE:SYMBOL")
    if not TICKER_RE.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {raw!r}")
    if exchange and not TICKER_RE.fullmatch(exchange):
        raise ValueError(f"invalid exchange qualifier: {exchange!r}")
    return TickerIdentifier(ticker=ticker, exchange_qualifier=exchange)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"missing configuration file: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return value


def _require_string(value: dict[str, Any], key: str, path: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{path}: {key!r} must be a non-empty string")
    return item.strip()


def _require_string_list(value: dict[str, Any], key: str, path: Path, *, allow_empty: bool = False) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list) or (not allow_empty and not items):
        raise ValueError(f"{path}: {key!r} must be {'a list' if allow_empty else 'a non-empty list'}")
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError(f"{path}: {key!r} must contain only non-empty strings")
    return [item.strip() for item in items]


def _validate_defaults(defaults: dict[str, Any], path: Path) -> None:
    for key in ("default_days", "intent", "freshness_mode", "cluster_mode", "output_directory"):
        _require_string(defaults, key, path) if key != "default_days" else None
    if not isinstance(defaults.get("default_days"), int) or defaults["default_days"] < 1:
        raise ValueError(f"{path}: default_days must be a positive integer")
    _require_string_list(defaults, "sources", path)
    backend = defaults.get("reddit_backend")
    if backend not in {"auto", "scrapecreators"}:
        raise ValueError(f"{path}: reddit_backend must be 'auto' or 'scrapecreators'")
    execution = defaults.get("reddit_execution")
    if not isinstance(execution, dict):
        raise ValueError(f"{path}: reddit_execution must be an object")
    for key in ("max_queries_per_group", "max_queries_per_company", "max_comment_trees_per_company", "max_discovered_subreddits_per_group", "max_subreddit_expansion_requests_per_group"):
        if not isinstance(execution.get(key), int) or execution[key] < 1:
            raise ValueError(f"{path}: reddit_execution.{key} must be a positive integer")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def resolve_run(identifier: TickerIdentifier, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a company online; no local ticker/company configuration is read."""
    defaults = _read_object(DEFAULTS_PATH)
    _validate_defaults(defaults, DEFAULTS_PATH)
    identity = resolve_ticker(identifier.ticker)
    entities = discover_company_entities(identity)
    if not entities:
        raise ValueError(f"could not discover company entities for {identity.company_name}")
    config = _deep_merge(defaults, {"company_name": identity.company_name, "exchange": identity.exchange or "", "market": identity.industry or "", "search_groups": _generic_groups(identity)})
    config = _deep_merge(config, {key: value for key, value in (overrides or {}).items() if value is not None})
    return {"config": config, "identity": identity, "entities": entities, "defaults_path": DEFAULTS_PATH, "reddit_policy": reddit_policy.load_policy(BLOCKLIST_PATH, PREFERENCES_PATH)}

def parse_as_of(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be YYYY-MM-DD") from exc


def result_paths(ticker: str, output_directory: str, as_of: date, days: int) -> tuple[Path, Path, str, str]:
    start = as_of - timedelta(days=days - 1)
    stem = f"{ticker}-reddit-{start.isoformat()}-to-{as_of.isoformat()}"
    directory = (REPO_ROOT / output_directory).resolve()
    return directory / f"{stem}.md", directory / f"{stem}.json", start.isoformat(), as_of.isoformat()


def build_engine_plan(resolved: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    """Expand each explicit query into an engine subquery without narrowing Reddit."""
    config = resolved["config"]
    groups: list[dict[str, Any]] = []
    for group in config["search_groups"]:
        group_queries: list[dict[str, str]] = []
        for index, query in enumerate(group["queries"], start=1):
            label = f"{group['id']}-{index}"
            group_queries.append({
                "label": label,
                "group_id": group["id"],
                "query": query,
            })
        groups.append({"group_id": group["id"], "queries": group_queries, "ranking_query": group["ranking_query"], "weight": group.get("weight", 1.0)})
    budget = config["reddit_execution"]
    schedule = schedule_group_queries(
        groups,
        max_queries_per_group=budget["max_queries_per_group"],
        max_queries_per_company=budget["max_queries_per_company"],
    )
    group_by_id = {group["group_id"]: group for group in groups}
    queries = schedule["scheduled"]
    subqueries = [{
        "label": query["label"], "group_id": query["group_id"], "search_query": query["query"],
        "ranking_query": group_by_id[query["group_id"]]["ranking_query"], "sources": config["sources"],
        "weight": group_by_id[query["group_id"]]["weight"],
    } for query in queries]
    return {
        "intent": config["intent"],
        "freshness_mode": config["freshness_mode"],
        "cluster_mode": config["cluster_mode"],
        "reddit_comment_enrichment_mode": "deferred_company_wide",
        "reddit_comment_tree_budget": budget["max_comment_trees_per_company"],
        "reddit_search_execution_mode": "group_scoped_subreddit_expansion",
        "reddit_max_discovered_subreddits_per_group": budget["max_discovered_subreddits_per_group"],
        "reddit_max_subreddit_expansion_requests_per_group": budget["max_subreddit_expansion_requests_per_group"],
        "preserve_all_subqueries": True,
        "company_query_budget": budget["max_queries_per_company"],
        "reddit_entity_terms": engine_pipeline.company_title_eligibility_entities(
            resolved["entities"], resolved["identity"].ticker,
        ),
        "subqueries": subqueries,
    }, queries, {"strategy": "deterministic_round_robin", **budget, "configured_query_count": sum(len(group["queries"]) for group in groups), "scheduled_query_count": len(queries), "skipped_query_count": len(schedule["skipped"]), "submission_order": [query["label"] for query in queries], "skipped_queries": schedule["skipped"]}


def complete_plan(identifier: TickerIdentifier, resolved: dict[str, Any], as_of: date, days: int) -> dict[str, Any]:
    config = resolved["config"]
    policy = resolved["reddit_policy"]
    markdown_path, metadata_path, start, end = result_paths(identifier.ticker, config["output_directory"], as_of, days)
    engine_plan, queries, scheduler = build_engine_plan(resolved)
    return {
        "ticker": identifier.ticker,
        "company_name": config["company_name"], "resolved_company_name": resolved["identity"].company_name, "resolved_short_name": resolved["identity"].short_name, "resolved_exchange": resolved["identity"].exchange, "discovered_entities": resolved["entities"],
        "exchange": config["exchange"],
        "market": config["market"],
        "date_range": {"from": start, "to": end, "days": days},
        "global_defaults_path": str(resolved["defaults_path"]),
        "sources": config["sources"],
        "intent": config["intent"],
        "freshness_mode": config["freshness_mode"],
        "cluster_mode": config["cluster_mode"],
        "reddit_backend": config["reddit_backend"],
        "global_reddit_blocklist": {
            "path": policy.blocklist_path,
            "version": policy.blocklist_version,
            "blocked_subreddits": sorted(policy.blocked_subreddits),
        },
        "global_reddit_preferences": {
            "path": policy.preferences_path,
            "version": policy.preferences_version,
            "preferred_subreddits": policy.preferred_subreddits,
        },
        "resolved_search_groups": config["search_groups"],
        "research_group_count": len(config["search_groups"]),
        "configured_query_count": scheduler["configured_query_count"],
        "groups": [
            {"group_id": group["id"], "subreddit_expansion_query": group["ranking_query"], "query_labels": [query["label"] for query in queries if query["group_id"] == group["id"]]}
            for group in config["search_groups"]
        ],
        "queries": queries,
        "query_scheduler": scheduler,
        "search_execution_mode": "query_scoped_flat",
        "subreddit_discovery_mode": "per_query_existing_behavior",
        "comment_enrichment": {
            "mode": "deferred_company_wide",
            "strategy": "deterministic_group_round_robin_preliminary_quality",
            "maximum_unique_comment_tree_requests": config["reddit_execution"]["max_comment_trees_per_company"],
            "maximum_unique_posts_selected_for_comment_enrichment": config["reddit_execution"]["max_comment_trees_per_company"],
            "maximum_scrapecreators_comment_attempts": config["reddit_execution"]["max_comment_trees_per_company"],
            "maximum_public_comment_attempts": config["reddit_execution"]["max_comment_trees_per_company"],
            "maximum_total_comment_network_attempts": config["reddit_execution"]["max_comment_trees_per_company"] * 2,
            "maximum_total_comment_network_attempts_without_scrapecreators_key": config["reddit_execution"]["max_comment_trees_per_company"],
            "deduplication_key": "genuine_post_id_or_canonical_url",
            "cache_scope": "single_company_run",
            "note": "Comment enrichment is deferred; provisional posts are not renderable until Phase 3C.",
        },
        "execution_estimate": build_execution_estimate(engine_plan, queries),
        "engine_plan": engine_plan,
        "markdown_output_path": str(markdown_path),
        "metadata_output_path": str(metadata_path),
        "broad_reddit_discovery": True,
    }


def build_execution_estimate(engine_plan: dict[str, Any], queries: list[dict[str, str]], depth: str = "default") -> dict[str, Any]:
    """Describe bounded Reddit work from a plan without making any requests."""
    reddit_queries = [query for query in queries if "reddit" in next(
        subquery["sources"] for subquery in engine_plan["subqueries"] if subquery["label"] == query["label"]
    )]
    profile = engine_reddit.DEPTH_CONFIG[depth]
    group_ids = list(dict.fromkeys(query["group_id"] for query in reddit_queries if query.get("group_id")))
    group_mode = engine_plan.get("reddit_search_execution_mode") == "group_scoped_subreddit_expansion"
    expansion_per_group = min(
        int(engine_plan.get("reddit_max_discovered_subreddits_per_group") or 3),
        int(engine_plan.get("reddit_max_subreddit_expansion_requests_per_group") or 3),
    ) if group_mode else profile["subreddit_searches"]
    base_global = len(reddit_queries) * profile["global_searches"]
    expansion = len(group_ids) * expansion_per_group if group_mode else len(reddit_queries) * expansion_per_group
    return {
        "reddit_subquery_count": len(reddit_queries),
        "scheduled_base_query_count": len(reddit_queries),
        "research_group_count": len(group_ids),
        "maximum_base_global_search_requests": base_global,
        "maximum_subreddit_discovery_passes": len(group_ids) if group_mode else len(reddit_queries),
        "maximum_subreddit_expansion_requests": expansion,
        "maximum_primary_search_requests": base_global + expansion,
        "maximum_public_fallback_search_invocations": len(reddit_queries) + expansion,
        "maximum_fallback_search_invocations": len(reddit_queries) + expansion,
        "comment_enrichment_is_company_wide": True,
        "source_fetch_cap": engine_pipeline.MAX_SOURCE_FETCHES.get("reddit"),
        "queries_expected_to_execute": [query["label"] for query in reddit_queries],
        "queries_expected_to_be_skipped": [],
        "submission_order": [query["label"] for query in reddit_queries],
        "broad_discovery": True,
    }


def _read_run_summary(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file() or path.stat().st_size == 0:
        return None, "engine did not write run summary"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"malformed run summary JSON: {exc.msg}"
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None, "invalid run summary schema"
    return payload, None


def _write_metadata(plan: dict[str, Any], status: str, returncode: int | None, statistics: dict[str, Any] | None, summary_error: str | None) -> None:
    markdown_path = Path(plan["markdown_output_path"])
    metadata_path = Path(plan["metadata_output_path"])
    payload = {
        "ticker": plan["ticker"], "company_name": plan["company_name"],
        "exchange": plan["exchange"], "market": plan["market"],
        "date_range": plan["date_range"],
        "config_paths": {"global_defaults": plan["global_defaults_path"]},
        "executed_search_groups": plan["resolved_search_groups"],
        "research_group_count": plan["research_group_count"],
        "configured_query_count": plan["configured_query_count"],
        "groups": plan["groups"],
        "executed_queries": plan["queries"], "reddit_backend": plan["reddit_backend"],
        "query_scheduler": plan["query_scheduler"],
        "execution_estimate": plan["execution_estimate"],
        "search_execution_mode": plan["search_execution_mode"],
        "subreddit_discovery_mode": plan["subreddit_discovery_mode"],
        "comment_enrichment": plan["comment_enrichment"],
        "global_reddit_blocklist": plan["global_reddit_blocklist"],
        "global_reddit_preferences": plan["global_reddit_preferences"],
        "statistics": statistics,
        "run_summary_error": summary_error,
        "markdown_path": str(markdown_path), "execution_status": status,
        "engine_returncode": returncode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ticker-first broad-discovery Reddit research.")
    identifiers = parser.add_mutually_exclusive_group(required=True)
    identifiers.add_argument("ticker", nargs="?", help="Ticker symbol, e.g. ADBE")
    identifiers.add_argument("--ticker", dest="ticker_flag", help="Ticker symbol, e.g. ADBE")
    parser.add_argument("--days", type=int, help="Inclusive research window in days")
    parser.add_argument("--as-of", type=parse_as_of, default=date.today(), help="Research end date (YYYY-MM-DD)")
    parser.add_argument("--sources", help="Comma-separated source override")
    parser.add_argument("--intent", help="Intent override")
    parser.add_argument("--freshness-mode", help="Freshness mode override")
    parser.add_argument("--cluster-mode", help="Cluster mode override")
    parser.add_argument("--output-directory", help="Output-directory override, relative to repository root")
    parser.add_argument("--reddit-backend", choices=["auto", "scrapecreators"], help="Reddit backend override")
    parser.add_argument("--plan", action="store_true", help="Print the complete resolved run plan and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        identifier = normalize_ticker(args.ticker or args.ticker_flag)
        overrides: dict[str, Any] = {
            "default_days": args.days, "intent": args.intent, "freshness_mode": args.freshness_mode,
            "cluster_mode": args.cluster_mode, "output_directory": args.output_directory,
            "reddit_backend": args.reddit_backend,
        }
        if args.sources is not None:
            overrides["sources"] = [item.strip() for item in args.sources.split(",") if item.strip()]
        resolved = resolve_run(identifier, overrides)
        days = resolved["config"]["default_days"]
        if not isinstance(days, int) or days < 1:
            raise ValueError("resolved default_days must be at least 1")
        plan = complete_plan(identifier, resolved, args.as_of, days)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(f"research_company: {exc}") from exc
    if args.plan:
        print(json.dumps(plan, indent=2) + "\n")
        return 0

    markdown_path = Path(plan["markdown_output_path"])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path: Path | None = None
    summary_path: Path | None = None
    returncode: int | None = None
    statistics: dict[str, Any] | None = None
    summary_error: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", prefix=f".{identifier.ticker}-plan-", dir=markdown_path.parent, delete=False) as handle:
            json.dump(plan["engine_plan"], handle)
            plan_path = Path(handle.name)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", prefix=f".{identifier.ticker}-summary-", dir=markdown_path.parent, delete=False) as handle:
            summary_path = Path(handle.name)
        command = [
            sys.executable, str(ENGINE_PATH), identifier.ticker, "--search", ",".join(plan["sources"]),
            "--plan", str(plan_path), "--emit", "md", "--days", str(days), "--as-of", args.as_of.isoformat(),
            "--run-summary-output", str(summary_path),
            "--output", str(markdown_path),
        ]
        # Do not pass --subreddits or --dedicated-subreddits: either flag turns
        # Last30Days' global discovery into a targeted/floor-exempt lane.
        environment = os.environ.copy()
        environment[reddit_policy.BLOCKLIST_ENV_VAR] = str(BLOCKLIST_PATH)
        environment[reddit_policy.PREFERENCES_ENV_VAR] = str(PREFERENCES_PATH)
        if plan["reddit_backend"] == "scrapecreators":
            environment["LAST30DAYS_REDDIT_BACKEND"] = "scrapecreators"
        returncode = subprocess.run(command, cwd=REPO_ROOT, env=environment, check=False).returncode
        statistics, summary_error = _read_run_summary(summary_path)
        return returncode
    finally:
        if plan_path is not None:
            plan_path.unlink(missing_ok=True)
        if summary_path is not None:
            summary_path.unlink(missing_ok=True)
        if returncode != 0 and summary_error is None:
            summary_error = "engine returned a non-zero exit code"
        _write_metadata(plan, "completed" if returncode == 0 else "failed", returncode, statistics, summary_error)


if __name__ == "__main__":
    raise SystemExit(main())
