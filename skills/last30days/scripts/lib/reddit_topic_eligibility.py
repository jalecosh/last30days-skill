"""Content-only eligibility and relevance components for Adobe Reddit plans.

Eligibility is title/original-body led. Comments and the matched subquery may
corroborate an already plausible post, but can never make an unrelated post pass.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from . import schema

ELIGIBILITY_THRESHOLD = 7.0
ADOBE_ELIGIBILITY_METADATA_KEYS = (
    "topic_eligible", "topic_eligibility_score",
    "topic_positive_signals", "topic_rejection_reasons",
    "title_entity_match", "body_entity_match",
    "relevant_comment_count", "usable_comment_count",
    "title_evidence", "original_body_evidence",
    "matched_subquery_support", "topic_centrality_score",
    "research_value_score", "final_relevance_score",
    "matched_subquery_label",
)

_DIRECT_PATTERNS: dict[str, re.Pattern[str]] = {
    "adobe": re.compile(r"\badobe\b", re.I),
    "adbe": re.compile(r"\badbe\b", re.I),
    "creative_cloud": re.compile(r"\bcreative\s+cloud\b", re.I),
    "photoshop": re.compile(r"\bphotoshop\b", re.I),
    "premiere_pro": re.compile(r"\bpremiere\s+pro\b", re.I),
    "after_effects": re.compile(r"\bafter\s+effects\b", re.I),
    "acrobat": re.compile(r"\bacrobat\b", re.I),
    "lightroom": re.compile(r"\blightroom\b", re.I),
    "indesign": re.compile(r"\bindesign\b", re.I),
    "adobe_stock": re.compile(r"\badobe\s+stock\b", re.I),
    "adobe_express": re.compile(r"\badobe\s+express\b", re.I),
    "substance_3d": re.compile(r"\bsubstance\s*3d\b", re.I),
}
_AMBIGUOUS_PATTERNS: dict[str, re.Pattern[str]] = {
    "firefly": re.compile(r"\bfirefly\b", re.I),
    "illustrator": re.compile(r"\billustrator\b", re.I),
    "stock": re.compile(r"\bstock\b", re.I),
    "express": re.compile(r"\bexpress\b", re.I),
    "substance": re.compile(r"\bsubstance\b", re.I),
    "premiere": re.compile(r"\bpremiere\b", re.I),
}
_AMBIGUOUS_CONTEXT = re.compile(
    r"\b(adobe|creative\s+cloud|photoshop|premiere\s+pro|after\s+effects|"
    r"acrobat|lightroom|indesign|firefly\s+(credits|pricing)|"
    r"(vector|design|workflow|replacement|alternative|subscription|license|pricing))\b",
    re.I,
)
_BUSINESS_OR_WORKFLOW = re.compile(
    r"\b(valuation|earnings|cash\s+flow|buyback|moat|subscription|pricing|price\s+increase|"
    r"cancellation|cancel|customer|credits?|generative|ai|copyright|workflow|replacement|"
    r"alternative|switching|license|licensing|ecosystem|margin|competition|compute)\b",
    re.I,
)
_RESEARCH_VALUE_POSITIVE = re.compile(
    r"\b(valuation|earnings?|revenue|margins?|free\s+cash\s+flow|cash\s+flow|buybacks?|"
    r"pricing|subscriptions?|cancellation(?:\s+policy)?|retention|churn|switching\s+costs?|"
    r"professional\s+workflows?|workflows?|ecosystem|lock-?in|competitors?|replacements?|"
    r"firefly|generative\s+ai|copyright|compute\s+costs?|product\s+quality|"
    r"customer\s+sentiment|business\s+strategy)\b",
    re.I,
)
_RESEARCH_VALUE_LOW = re.compile(
    r"\b(pirat(?:e|ing|ed)|download|crack(?:ing|ed)?|activation|install(?:ation|ing)?|"
    r"troubleshoot(?:ing)?|how\s+do\s+i\s+use|showcase|my\s+art(?:work)?|meme)\b",
    re.I,
)
_LIST_MARKER = re.compile(r"[,;/]|\b(and|or|plus|including)\b", re.I)
_SOFTWARE_LIST_CONTEXT = re.compile(r"\b(software|apps?|programs?|tools?|piracy|cracks?)\b", re.I)
_INCIDENTAL_CREDIT_CONTEXT = re.compile(r"\b(image\s+credit|credits?:|edited\s+(in|with)|username|watermark)\b", re.I)


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    score: float
    positive_signals: list[str]
    rejection_reasons: list[str]
    title_entity_match: bool
    body_entity_match: bool
    relevant_comment_count: int
    usable_comment_count: int
    title_evidence: list[str]
    original_body_evidence: list[str]
    matched_subquery_support: float


def is_adobe_focused_text(value: object) -> bool:
    """Return whether a topic/plan text asks about Adobe or a named product."""
    text = str(value or "")
    return bool(
        any(pattern.search(text) for pattern in _DIRECT_PATTERNS.values())
        or re.search(r"\b(adobe|adbe|firefly|illustrator)\b", text, re.I)
    )


def _plain_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()


def _direct_matches(text: str) -> list[str]:
    return [name for name, pattern in _DIRECT_PATTERNS.items() if pattern.search(text)]


def _match_count(text: str, patterns: dict[str, re.Pattern[str]]) -> int:
    return sum(len(pattern.findall(text)) for pattern in patterns.values())


def _qualified_ambiguous_matches(text: str) -> tuple[list[str], list[str]]:
    present = [name for name, pattern in _AMBIGUOUS_PATTERNS.items() if pattern.search(text)]
    if not present:
        return [], []
    return (present if _AMBIGUOUS_CONTEXT.search(text) else []), present


def _comment_texts(item: schema.SourceItem) -> list[str]:
    comments: list[str] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        text = _plain_text(node.get("body") or node.get("excerpt") or node.get("text"))
        if text:
            comments.append(text)
        for child in node.get("replies") or node.get("children") or node.get("comments") or []:
            visit(child)

    tree = item.metadata.get("comment_tree") or item.metadata.get("comments") or []
    if isinstance(tree, list):
        for node in tree:
            visit(node)
    if not comments:
        for node in item.metadata.get("top_comments") or []:
            visit(node)
    return comments


def _is_broad_software_list(text: str, direct_count: int) -> bool:
    product_mentions = len(_direct_matches(text)) + len(_qualified_ambiguous_matches(text)[0])
    return direct_count <= 1 and bool(_LIST_MARKER.search(text)) and (
        product_mentions >= 3 or bool(_SOFTWARE_LIST_CONTEXT.search(text))
    )


def _subquery_support(title: str, body: str, search_query: str, ranking_query: str) -> float:
    """A weak corroborator that is intentionally incapable of passing the gate."""
    post_tokens = set(re.findall(r"[a-z0-9]{4,}", f"{title} {body}".lower()))
    query_tokens = set(re.findall(r"[a-z0-9]{4,}", f"{search_query} {ranking_query}".lower()))
    query_tokens -= {"adobe", "creative", "cloud", "reddit"}
    return 0.5 if len(post_tokens & query_tokens) >= 2 else 0.0


def evaluate(item: schema.SourceItem, *, subquery_label: str, search_query: str, ranking_query: str) -> EligibilityResult:
    """Evaluate Adobe centrality from title and original Reddit post body only."""
    title = _plain_text(item.title)
    # Normalized Reddit `item.body` contains comments. Eligibility must never
    # fall back to it because comment-only evidence cannot establish centrality.
    post_body = _plain_text(item.metadata.get("reddit_post_body"))
    comments = _comment_texts(item)

    title_direct = _direct_matches(title)
    title_ambiguous, title_ambiguous_present = _qualified_ambiguous_matches(title)
    body_direct = _direct_matches(post_body)
    body_ambiguous, body_ambiguous_present = _qualified_ambiguous_matches(post_body)
    title_entity_match = bool(title_direct or title_ambiguous)
    body_entity_match = bool(body_direct or body_ambiguous)
    title_evidence = [*title_direct, *title_ambiguous]
    body_evidence = [*body_direct, *body_ambiguous]
    body_direct_count = _match_count(post_body, _DIRECT_PATTERNS)
    subquery_support = _subquery_support(title, post_body, search_query, ranking_query)

    relevant_comments = 0
    for comment in comments:
        direct = _direct_matches(comment)
        qualified, _present = _qualified_ambiguous_matches(comment)
        if direct or qualified:
            relevant_comments += 1

    score = 0.0
    positives: list[str] = []
    rejection: list[str] = []
    if title_direct:
        score += 10.0
        positives.append("strong_entity_or_product_in_title")
    elif title_ambiguous:
        score += 4.0
        positives.append("contextual_product_in_title")
    if body_direct:
        # Repetition and distinct named products make a generic title credible.
        score += 4.0 + min(5.0, float(max(0, body_direct_count - 1)) * 2.0)
        if len(body_direct) >= 2:
            score += 2.0
        positives.append("strong_entity_or_product_in_original_body")
    elif body_ambiguous:
        score += 2.0
        positives.append("contextual_product_in_original_body")
    if (title_entity_match or body_entity_match) and _BUSINESS_OR_WORKFLOW.search(title + " " + post_body):
        score += 2.0
        positives.append("business_or_workflow_context")
    if relevant_comments >= 2:
        score += min(1.5, relevant_comments * 0.5)
        positives.append("sustained_adobe_centered_comments")
    elif relevant_comments == 1 and (title_entity_match or body_entity_match):
        score += 0.5
        positives.append("supporting_adobe_comment")
    if subquery_support:
        score += subquery_support
        positives.append("matched_subquery_support")

    isolated_ambiguous = set(title_ambiguous_present + body_ambiguous_present) - set(title_ambiguous + body_ambiguous)
    if isolated_ambiguous:
        score -= 4.0
        rejection.append("ambiguous_product_term_without_adobe_context")
    direct_count = len(title_direct) + len(body_direct)
    if _is_broad_software_list(title + " " + post_body, direct_count):
        score -= 6.0
        rejection.append("broad_software_list_or_incidental_mention")
    if len(title_direct) == 1 and not body_direct and _INCIDENTAL_CREDIT_CONTEXT.search(title + " " + post_body):
        score -= 6.0
        rejection.append("incidental_credit_or_edit_note")
    if not (title_entity_match or body_entity_match):
        rejection.append("comment_only_or_incidental_mention")
    if not (title_entity_match or body_entity_match or relevant_comments):
        rejection.append("no_adobe_entity_or_product_signal")

    # A direct title can establish a central Adobe subject. A generic title
    # needs repeated/distinct body evidence, or one direct body mention plus
    # several business/workflow signals, before comments can support it.
    body_context_count = len(_BUSINESS_OR_WORKFLOW.findall(post_body))
    body_is_meaningful = bool(body_direct) and (
        body_direct_count >= 2
        or len(body_direct) >= 2
        or (body_direct_count >= 1 and body_context_count >= 3)
    )
    title_is_strong = bool(title_direct)
    eligible = score >= ELIGIBILITY_THRESHOLD and (title_is_strong or body_is_meaningful) and (
        "broad_software_list_or_incidental_mention" not in rejection
    )
    if body_entity_match and not body_is_meaningful and not title_is_strong:
        rejection.append("insufficient_original_body_evidence")
    if not eligible and not rejection:
        rejection.append("insufficient_central_adobe_evidence")

    result = EligibilityResult(
        eligible=eligible,
        score=round(score, 2),
        positive_signals=positives,
        rejection_reasons=rejection,
        title_entity_match=title_entity_match,
        body_entity_match=body_entity_match,
        relevant_comment_count=relevant_comments,
        usable_comment_count=len(comments),
        title_evidence=title_evidence,
        original_body_evidence=body_evidence,
        matched_subquery_support=subquery_support,
    )
    item.metadata.update({
        "topic_eligible": result.eligible,
        "topic_eligibility_score": result.score,
        "topic_positive_signals": result.positive_signals,
        "topic_rejection_reasons": result.rejection_reasons,
        "title_entity_match": result.title_entity_match,
        "body_entity_match": result.body_entity_match,
        "relevant_comment_count": result.relevant_comment_count,
        "usable_comment_count": result.usable_comment_count,
        "title_evidence": result.title_evidence,
        "original_body_evidence": result.original_body_evidence,
        "matched_subquery_support": result.matched_subquery_support,
        "matched_subquery_label": subquery_label,
        "matched_search_query": search_query,
        "matched_ranking_query": ranking_query,
    })
    return result


def relevance_components(item: schema.SourceItem, lexical_relevance: float) -> tuple[float, float, float]:
    """Return topic centrality, research value, and the 70/30 relevance blend."""
    metadata = item.metadata
    title = _plain_text(item.title)
    body = _plain_text(metadata.get("reddit_post_body"))
    comments = _comment_texts(item)
    title_evidence = metadata.get("title_evidence") or []
    body_evidence = metadata.get("original_body_evidence") or []
    direct_body_count = _match_count(body, _DIRECT_PATTERNS)

    centrality = 0.0
    if title_evidence:
        centrality += 0.55 if any(e in _DIRECT_PATTERNS for e in title_evidence) else 0.35
    if body_evidence:
        centrality += min(0.35, 0.18 + 0.08 * direct_body_count + 0.05 * len(body_evidence))
    centrality += min(0.07, int(metadata.get("relevant_comment_count") or 0) * 0.025)
    centrality += float(metadata.get("matched_subquery_support") or 0.0) * 0.06
    centrality = max(0.0, min(1.0, centrality * 0.85 + max(0.0, min(1.0, lexical_relevance)) * 0.15))

    text = f"{title} {body} {' '.join(comments)}"
    positive = len(_RESEARCH_VALUE_POSITIVE.findall(text))
    low_value = len(_RESEARCH_VALUE_LOW.findall(text))
    research_value = min(1.0, 0.18 + positive * 0.14)
    if low_value:
        research_value = max(0.05, research_value - min(0.45, low_value * 0.18))
    final_relevance = max(0.0, min(1.0, 0.70 * centrality + 0.30 * research_value))
    return round(centrality, 4), round(research_value, 4), round(final_relevance, 4)


def rejection_counter(results: list[EligibilityResult]) -> dict[str, int]:
    return dict(Counter(reason for result in results for reason in result.rejection_reasons))
