from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.models.internal import SearchResponse


GENERIC_PHRASES = {
    "it depends",
    "there are several factors",
    "consider your needs",
    "the best choice depends",
    "in conclusion",
    "overall, it is important",
}
CERTAINTY_PHRASES = {
    "definitely",
    "certainly",
    "always",
    "never",
    "clearly",
    "without doubt",
}
UNCERTAINTY_PHRASES = {
    "unclear",
    "mixed evidence",
    "conflicting",
    "may",
    "might",
    "can vary",
    "uncertain",
    "limited evidence",
}
CONTRADICTION_TERMS = {
    "however",
    "but",
    "while",
    "although",
    "conflicting",
    "disagree",
    "mixed",
    "on the other hand",
}


@dataclass
class GateResult:
    name: str
    passed: bool
    reason: str


@dataclass
class EvalResult:
    fixture_name: str
    passed: bool
    gates: list[GateResult]


def _text(response: SearchResponse) -> str:
    findings_text = " ".join(finding.claim for finding in response.findings)
    return " ".join([response.direct_answer, response.summary, findings_text]).strip().lower()


def _query_terms(query: str) -> set[str]:
    return {token for token in query.lower().replace("?", " ").replace("/", " ").split() if len(token) > 3}


def _citation_terms(response: SearchResponse) -> set[str]:
    tokens: set[str] = set()
    for citation in response.citations:
        text = " ".join([citation.title, citation.excerpt, citation.source]).lower()
        for token in text.replace("/", " ").replace("-", " ").split():
            clean = token.strip(".,:;()[]{}\"'")
            if len(clean) > 3:
                tokens.add(clean)
    return tokens


def _count_supported_findings(response: SearchResponse) -> int:
    citation_ids = {citation.id for citation in response.citations}
    return sum(1 for finding in response.findings if citation_ids.intersection(finding.citation_ids))


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def generic_answer_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    text = _text(response)
    query_terms = _query_terms(response.query)
    grounded_terms = _citation_terms(response)
    overlap = len(query_terms & grounded_terms)
    min_overlap = int(rules.get("min_query_term_overlap", 1))

    if _contains_any(text, GENERIC_PHRASES):
        return GateResult("generic_answer", False, "generic filler phrase detected")
    if overlap < min_overlap:
        return GateResult("generic_answer", False, f"only {overlap} grounded query terms overlapped")
    return GateResult("generic_answer", True, f"grounded overlap={overlap}")


def thin_answer_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    min_findings = int(rules.get("min_findings", 1))
    min_summary_chars = int(rules.get("min_summary_chars", 80))
    if len(response.findings) < min_findings:
        return GateResult("thin_answer", False, f"expected at least {min_findings} findings")
    if len(response.summary.strip()) < min_summary_chars:
        return GateResult("thin_answer", False, f"summary shorter than {min_summary_chars} chars")
    return GateResult("thin_answer", True, f"findings={len(response.findings)} summary_chars={len(response.summary.strip())}")


def grounding_citation_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    min_citations = int(rules.get("min_citations", 1))
    min_supported_findings = int(rules.get("min_supported_findings", min_citations))
    supported_findings = _count_supported_findings(response)
    if len(response.citations) < min_citations:
        return GateResult("grounding_citation", False, f"expected at least {min_citations} citations")
    if supported_findings < min_supported_findings:
        return GateResult("grounding_citation", False, f"only {supported_findings} findings have citation support")
    return GateResult("grounding_citation", True, f"citations={len(response.citations)} supported_findings={supported_findings}")


def contradiction_handling_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    text = _text(response)
    required = bool(rules.get("required", False))
    if not required:
        return GateResult("contradiction_handling", True, "not required for fixture")
    if _contains_any(text, CONTRADICTION_TERMS):
        return GateResult("contradiction_handling", True, "response acknowledges conflicting evidence")
    return GateResult("contradiction_handling", False, "missing contradiction or tradeoff language")


def unsupported_certainty_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    text = _text(response)
    max_confidence = str(rules.get("max_confidence", "high"))
    require_uncertainty = bool(rules.get("require_uncertainty", False))
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    if confidence_rank.get(response.confidence, 0) > confidence_rank.get(max_confidence, 2):
        return GateResult("unsupported_certainty", False, f"confidence {response.confidence} exceeds {max_confidence}")
    if require_uncertainty and not _contains_any(text, UNCERTAINTY_PHRASES):
        return GateResult("unsupported_certainty", False, "missing uncertainty language")
    if require_uncertainty and _contains_any(text, CERTAINTY_PHRASES):
        return GateResult("unsupported_certainty", False, "certainty language too strong for sparse evidence")
    return GateResult("unsupported_certainty", True, f"confidence={response.confidence}")


def regression_baseline_gate(response: SearchResponse, rules: dict[str, Any]) -> GateResult:
    baseline = rules.get("baseline") or {}
    min_citations = int(baseline.get("min_citations", 0))
    min_findings = int(baseline.get("min_findings", 0))
    min_summary_chars = int(baseline.get("min_summary_chars", 0))
    if len(response.citations) < min_citations:
        return GateResult("regression_baseline", False, f"citations regressed below baseline {min_citations}")
    if len(response.findings) < min_findings:
        return GateResult("regression_baseline", False, f"findings regressed below baseline {min_findings}")
    if len(response.summary.strip()) < min_summary_chars:
        return GateResult("regression_baseline", False, f"summary regressed below baseline {min_summary_chars} chars")
    return GateResult("regression_baseline", True, "meets baseline thresholds")


def score_response(response: SearchResponse, rules: dict[str, Any]) -> EvalResult:
    gates = [
        generic_answer_gate(response, rules.get("generic_answer", {})),
        thin_answer_gate(response, rules.get("thin_answer", {})),
        grounding_citation_gate(response, rules.get("grounding_citation", {})),
        contradiction_handling_gate(response, rules.get("contradiction_handling", {})),
        unsupported_certainty_gate(response, rules.get("unsupported_certainty", {})),
        regression_baseline_gate(response, rules.get("regression_baseline", {})),
    ]
    return EvalResult(
        fixture_name=str(rules.get("fixture_name", response.query)),
        passed=all(gate.passed for gate in gates),
        gates=gates,
    )
