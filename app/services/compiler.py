from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

import httpx


logger = logging.getLogger(__name__)


class ResultCompiler:
    def __init__(self, enabled: bool, base_url: str, timeout_s: int, model_id: str):
        self.enabled = enabled
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s
        self.model_id = model_id.strip()

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.base_url and self.model_id)

    async def compile_perplexity_results(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        max_results: int,
        max_tokens_per_page: int | None,
    ) -> List[Dict[str, Any]]:
        if not self.active or not candidates:
            return candidates

        prompt = self._build_prompt(query, candidates, max_results, max_tokens_per_page)
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict grounding compiler for web search results. "
                        "You must only use provided candidate evidence. "
                        "Never invent facts, URLs, dates, or entities. "
                        "Return strict JSON only, with each result mapped to citation_ids from candidates."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }

        headers = {"Content-Type": "application/json"}
        token = os.getenv("EWS_COMPILER_API_KEY", "") or os.getenv("LITELLM_API_KEY", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        endpoint = self._chat_endpoint()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code >= 400:
                body_preview = resp.text[:400].replace("\n", " ") if resp.text else ""
                logger.warning(
                    "event=compiler_http_error status=%s model=%s endpoint=%s body=%r",
                    resp.status_code,
                    self.model_id,
                    endpoint,
                    body_preview,
                )
                return candidates

            body = resp.json()
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            parsed = self._parse_compiler_content(content)
            if not isinstance(parsed.get("results"), list) or not parsed.get("results"):
                retry_content = await self._retry_for_json_only(query, candidates, endpoint, headers)
                if retry_content:
                    parsed = self._parse_compiler_content(retry_content)

            results = parsed.get("results", [])
            normalized = self._normalize_results(results, candidates)
            return normalized[:max_results] if normalized else candidates
        except Exception as exc:
            logger.warning(
                "event=compiler_error model=%s endpoint=%s error_type=%s error=%r",
                self.model_id,
                endpoint,
                type(exc).__name__,
                exc,
            )
            return candidates

    async def vet_research_response(
        self,
        query: str,
        response: Dict[str, Any],
        fallback_query: str | None = None,
    ) -> Dict[str, Any] | None:
        if not self.active:
            return None

        diagnostics = response.get("diagnostics", {}) if isinstance(response, dict) else {}
        findings = response.get("findings", []) if isinstance(response, dict) else []
        citations = response.get("citations", []) if isinstance(response, dict) else []
        sources = response.get("sources", []) if isinstance(response, dict) else []

        prompt = json.dumps(
            {
                "task": "Assess whether a long-form research response is useful enough to return.",
                "query": query,
                "response": {
                    "direct_answer": response.get("direct_answer", "") if isinstance(response, dict) else "",
                    "summary": response.get("summary", "") if isinstance(response, dict) else "",
                    "findings_count": len(findings),
                    "citations_count": len(citations),
                    "sources_count": len(sources),
                    "confidence": response.get("confidence") if isinstance(response, dict) else None,
                    "warnings": diagnostics.get("warnings", []) if isinstance(diagnostics, dict) else [],
                    "errors": diagnostics.get("errors", []) if isinstance(diagnostics, dict) else [],
                },
                "fallback_query_hint": fallback_query,
                "rules": [
                    "Return JSON only.",
                    "useful must be true only if the answer is grounded, non-empty, and not generic.",
                    "If not useful, provide a short fallback_query for a fresh concise search.",
                    "Prefer a fallback query that narrows the query while preserving user intent.",
                ],
                "output_schema": {
                    "useful": "boolean",
                    "reason": "string",
                    "fallback_query": "string|null",
                },
            },
            ensure_ascii=True,
        )

        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict final quality gate for long-form web research. "
                        "You must only approve responses that are grounded, useful, and non-generic. "
                        "Return JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 180,
            "response_format": {"type": "json_object"},
        }

        headers = {"Content-Type": "application/json"}
        token = os.getenv("EWS_COMPILER_API_KEY", "") or os.getenv("LITELLM_API_KEY", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        endpoint = self._chat_endpoint()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code >= 400:
                body_preview = resp.text[:400].replace("\n", " ") if resp.text else ""
                logger.warning(
                    "event=research_vet_http_error status=%s model=%s endpoint=%s body=%r",
                    resp.status_code,
                    self.model_id,
                    endpoint,
                    body_preview,
                )
                return None

            body = resp.json()
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            parsed = self._parse_compiler_content(content)
            if not parsed:
                return None

            useful = parsed.get("useful")
            if isinstance(useful, str):
                useful = useful.strip().lower() in {"1", "true", "yes", "on"}
            if not isinstance(useful, bool):
                return None

            reason = parsed.get("reason")
            fallback = parsed.get("fallback_query")
            return {
                "useful": useful,
                "reason": str(reason).strip() if isinstance(reason, str) else "",
                "fallback_query": str(fallback).strip() if isinstance(fallback, str) and fallback.strip() else None,
            }
        except Exception as exc:
            logger.warning(
                "event=research_vet_error model=%s endpoint=%s error_type=%s error=%r",
                self.model_id,
                endpoint,
                type(exc).__name__,
                exc,
            )
            return None

    async def choose_search_profile(
        self,
        query: str,
        max_results: int,
        has_filters: bool,
        recency: bool,
        search_mode: str | None,
        heuristic: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if not self.active:
            return None

        prompt = json.dumps(
            {
                "task": "Choose best search profile for concise web search.",
                "query": query,
                "constraints": {
                    "max_results": max_results,
                    "has_filters": has_filters,
                    "recency": recency,
                    "search_mode": search_mode,
                },
                "heuristic_profile": heuristic,
                "allowed": {
                    "depth": ["quick", "balanced", "quality"],
                    "max_iterations": [1, 2],
                },
                "output_schema": {
                    "depth": "quick|balanced|quality",
                    "max_iterations": "1|2",
                    "reason": "string",
                },
                "rules": [
                    "Prefer low latency for broad queries.",
                    "Use quality only when clearly justified.",
                    "Return JSON only.",
                ],
            },
            ensure_ascii=True,
        )

        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict planner for web search profile selection. "
                        "Return JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 120,
            "response_format": {"type": "json_object"},
        }

        headers = {"Content-Type": "application/json"}
        token = os.getenv("EWS_COMPILER_API_KEY", "") or os.getenv("LITELLM_API_KEY", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        endpoint = self._chat_endpoint()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "event=planner_fallback_http_error status=%s model=%s endpoint=%s",
                    resp.status_code,
                    self.model_id,
                    endpoint,
                )
                return None

            body = resp.json()
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )

            parsed = self._parse_compiler_content(content)
            if not isinstance(parsed, dict):
                return None

            depth = parsed.get("depth")
            max_iterations = parsed.get("max_iterations")
            if isinstance(depth, str):
                depth = depth.strip().lower()
            if isinstance(max_iterations, str) and max_iterations.isdigit():
                max_iterations = int(max_iterations)

            if depth not in {"quick", "balanced", "quality"}:
                return None
            if max_iterations not in {1, 2}:
                return None

            return {
                "mode": "fast",
                "depth": depth,
                "max_iterations": max_iterations,
            }
        except Exception as exc:
            logger.warning(
                "event=planner_fallback_error model=%s endpoint=%s error_type=%s error=%r",
                self.model_id,
                endpoint,
                type(exc).__name__,
                exc,
            )
            return None

    def _chat_endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _build_prompt(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        max_results: int,
        max_tokens_per_page: int | None,
    ) -> str:
        candidate_lines = []
        for item in candidates[:12]:
            candidate_lines.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "title": item.get("title", "Untitled"),
                    "url": item.get("url", ""),
                    "snippet": str(item.get("snippet", ""))[:360],
                    "date": item.get("date"),
                    "last_updated": item.get("last_updated"),
                }
            )

        token_hint = max_tokens_per_page or 512
        spec = {
            "query": query,
            "max_results": max_results,
            "max_tokens_per_page": token_hint,
            "candidates": candidate_lines,
            "output_schema": {
                "results": [
                    {
                        "title": "string",
                        "url": "string",
                        "snippet": "string",
                        "date": "string|null",
                        "last_updated": "string|null",
                        "citation_ids": [0],
                        "evidence_spans": ["string"],
                        "confidence": 0.0,
                        "grounding_notes": "string|null",
                    }
                ]
            },
            "rules": [
                "Only use URLs from candidates.",
                "citation_ids must reference candidate_id values from candidates.",
                "citation_ids cannot be empty.",
                "No duplicate URLs.",
                "snippet must be concise and fully grounded in candidate snippets.",
                "Prefer synthesized, claim-like snippets over copied excerpts when possible.",
                "evidence_spans should be direct short excerpts from candidate snippets.",
                "Return JSON only.",
            ],
        }
        return json.dumps(spec, ensure_ascii=True)

    def _parse_compiler_content(self, content: Any) -> Dict[str, Any]:
        if isinstance(content, dict):
            if isinstance(content.get("results"), list):
                return content
            if content.get("depth") is not None and content.get("max_iterations") is not None:
                return content
            return {}

        if isinstance(content, list):
            return {"results": content}

        if not isinstance(content, str):
            return {}

        text = content.strip()
        if not text:
            return {}

        # Try direct JSON first.
        parsed = self._try_parse_json(text)
        if parsed:
            return parsed

        # Try fenced JSON blocks.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            parsed = self._try_parse_json(fence_match.group(1))
            if parsed:
                return parsed

        # Last attempt: extract outermost object/array slice.
        start_obj, end_obj = text.find("{"), text.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            parsed = self._try_parse_json(text[start_obj : end_obj + 1])
            if parsed:
                return parsed

        start_arr, end_arr = text.find("["), text.rfind("]")
        if start_arr != -1 and end_arr > start_arr:
            parsed = self._try_parse_json(text[start_arr : end_arr + 1])
            if parsed:
                return parsed

        logger.warning("event=compiler_parse_error preview=%r", text[:220])
        return {}

    async def _retry_for_json_only(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        endpoint: str,
        headers: Dict[str, str],
    ) -> str:
        compact_candidates = []
        for item in candidates[:8]:
            compact_candidates.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "url": item.get("url", ""),
                    "title": item.get("title", "Untitled"),
                    "snippet": str(item.get("snippet", ""))[:180],
                }
            )

        retry_payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return JSON only, no markdown, no prose. "
                        "Output format: {\"results\":[{\"title\":str,\"url\":str,\"snippet\":str,\"citation_ids\":[int]}]}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": query,
                            "candidates": compact_candidates,
                            "rules": [
                                "Only use candidate URLs.",
                                "citation_ids must refer to candidate_id.",
                                "snippet <= 40 words.",
                            ],
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                retry_resp = await client.post(endpoint, json=retry_payload, headers=headers)
            if retry_resp.status_code >= 400:
                logger.warning(
                    "event=compiler_retry_http_error status=%s model=%s endpoint=%s",
                    retry_resp.status_code,
                    self.model_id,
                    endpoint,
                )
                return ""

            retry_body = retry_resp.json()
            retry_content = retry_body.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(retry_content, list):
                retry_content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in retry_content
                )
            return retry_content if isinstance(retry_content, str) else ""
        except Exception as exc:
            logger.warning(
                "event=compiler_retry_error model=%s endpoint=%s error_type=%s error=%r",
                self.model_id,
                endpoint,
                type(exc).__name__,
                exc,
            )
            return ""

    def _try_parse_json(self, text: str) -> Dict[str, Any]:
        try:
            obj = json.loads(text)
        except Exception:
            return {}

        if isinstance(obj, dict):
            if isinstance(obj.get("results"), list):
                return obj
            return {}
        if isinstance(obj, list):
            return {"results": obj}
        return {}

    def _normalize_results(self, llm_results: List[Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_id = {
            int(item.get("candidate_id")): item
            for item in candidates
            if isinstance(item.get("candidate_id"), int)
        }
        by_url = {item.get("url", ""): item for item in candidates if item.get("url")}
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in llm_results:
            if not isinstance(item, dict):
                continue

            raw_ids = item.get("citation_ids", [])
            citation_ids: List[int] = []
            for cid in raw_ids if isinstance(raw_ids, list) else []:
                normalized_id = None
                if isinstance(cid, int):
                    normalized_id = cid
                elif isinstance(cid, str) and cid.isdigit():
                    normalized_id = int(cid)
                if normalized_id is not None and normalized_id in by_id:
                    citation_ids.append(normalized_id)

            url = str(item.get("url", "")).strip()
            if not url and citation_ids:
                url = str(by_id[citation_ids[0]].get("url", "")).strip()
            if not url or url not in by_url or url in seen:
                continue

            base = by_url[url]
            if not citation_ids:
                fallback_id = base.get("candidate_id")
                if isinstance(fallback_id, int):
                    citation_ids = [fallback_id]

            evidence_spans_raw = item.get("evidence_spans", [])
            evidence_spans = [
                str(span).strip()
                for span in evidence_spans_raw
                if isinstance(span, str) and span.strip()
            ][:3]
            if not evidence_spans:
                fallback_excerpt = str(base.get("snippet") or "").strip()
                if fallback_excerpt:
                    evidence_spans = [fallback_excerpt[:180]]

            confidence = item.get("confidence")
            confidence_value = None
            if isinstance(confidence, (int, float)):
                confidence_value = max(0.0, min(1.0, float(confidence)))

            seen.add(url)
            out.append(
                {
                    "title": str(item.get("title") or base.get("title") or "Untitled"),
                    "url": url,
                    "snippet": str(item.get("snippet") or base.get("snippet") or ""),
                    "date": item.get("date") or base.get("date"),
                    "last_updated": item.get("last_updated") or base.get("last_updated"),
                    "citation_ids": citation_ids,
                    "evidence_spans": evidence_spans,
                    "confidence": confidence_value,
                    "grounding_notes": (
                        str(item.get("grounding_notes")).strip()[:300]
                        if item.get("grounding_notes") is not None
                        else None
                    ),
                }
            )
        return out
