from __future__ import annotations

from typing import Any, Mapping

from app.models.internal import SearchResponse


_MIN_CITATIONS = 3
_MIN_SOURCES = 3
_MIN_FINDINGS = 2

# Keep the default vetting strict, but allow narrow lower-evidence cases that
# still carry enough grounded structure to avoid unnecessary fallback.
_RELAXED_MIN_CITATIONS = 2
_RELAXED_MIN_SOURCES = 2


def _payload_dict(payload: SearchResponse | Mapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, SearchResponse):
        return payload.model_dump()
    return dict(payload)


def _is_relaxed_but_grounded(data: dict[str, Any], citations: list[Any], sources: list[Any], findings: list[Any]) -> bool:
    mode = str(data.get("mode", "")).strip().lower()
    confidence = str(data.get("confidence", "")).strip().lower()
    summary = str(data.get("summary", "")).strip()
    direct_answer = str(data.get("direct_answer", "")).strip()
    body = str(data.get("body", "")).strip() or direct_answer

    if len(citations) < _RELAXED_MIN_CITATIONS or len(sources) < _RELAXED_MIN_SOURCES:
        return False

    if mode == "fast":
        return len(findings) >= 1 and len(summary) >= 80 and len(body) >= 24 and confidence in {"medium", "high"}

    return (
        len(findings) >= _MIN_FINDINGS
        and confidence in {"medium", "high"}
        and len(summary) >= 120
        and len(body) >= 40
    )


def looks_useful_search_response(payload: SearchResponse | Mapping[str, Any] | dict[str, Any]) -> bool:
    data = _payload_dict(payload)
    diagnostics = data.get("diagnostics", {})
    errors = diagnostics.get("errors", []) if isinstance(diagnostics, dict) else []
    citations = data.get("citations", []) or []
    sources = data.get("sources", []) or []
    findings = data.get("findings", []) or []
    direct_answer = str(data.get("direct_answer", "")).strip()
    summary = str(data.get("summary", "")).strip()
    body = str(data.get("body", "")).strip() or direct_answer
    confidence = str(data.get("confidence", "")).strip().lower()

    if errors:
        return False
    if not body or not summary:
        return False
    if confidence == "low":
        return False
    if len(citations) >= _MIN_CITATIONS and len(sources) >= _MIN_SOURCES and len(findings) >= _MIN_FINDINGS:
        return True
    return _is_relaxed_but_grounded(data, citations, sources, findings)
