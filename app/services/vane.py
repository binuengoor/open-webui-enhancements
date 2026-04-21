from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable

import httpx


logger = logging.getLogger(__name__)


class VaneClient:
    def __init__(
        self,
        enabled: bool,
        url: str,
        timeout_s: int,
        default_optimization_mode: str,
        chat_provider_id_env: str,
        chat_model_key: str,
        embedding_provider_id_env: str,
        embedding_model_key: str,
    ):
        self.enabled = enabled
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.default_optimization_mode = default_optimization_mode or "balanced"
        self.chat_provider_id_env = chat_provider_id_env
        self.chat_model_key = chat_model_key
        self.embedding_provider_id_env = embedding_provider_id_env
        self.embedding_model_key = embedding_model_key

    def _optimization_mode(self, depth: str) -> str:
        if depth == "quick":
            return "speed"
        if depth == "quality":
            return "quality"
        if depth == "balanced":
            return self.default_optimization_mode if self.default_optimization_mode in {"speed", "balanced", "quality"} else "balanced"
        return "balanced"

    def _timeout_for_depth(self, depth: str) -> int:
        if depth == "quick":
            return max(5, min(self.timeout_s, 20))
        if depth == "quality":
            return max(self.timeout_s, 90)
        return max(self.timeout_s, 45)

    def _extract_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [self._extract_text(item) for item in value]
            return "\n\n".join(part for part in parts if part)
        if isinstance(value, dict):
            for key in ("message", "answer", "summary", "content", "text", "output", "result"):
                text = self._extract_text(value.get(key))
                if text:
                    return text
        return ""

    def _pick_first_text(self, body: Dict[str, Any], paths: Iterable[tuple[str, ...]]) -> str:
        for path in paths:
            current: Any = body
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            text = self._extract_text(current)
            if text:
                return text
        return ""

    def _extract_sources(self, body: Dict[str, Any]) -> list[Dict[str, Any]]:
        sources = []
        raw_sources = body.get("sources", [])
        if not isinstance(raw_sources, list):
            return sources
        for src in raw_sources:
            if not isinstance(src, dict):
                continue
            meta = src.get("metadata", {}) if isinstance(src.get("metadata"), dict) else {}
            sources.append(
                {
                    "title": meta.get("title") or src.get("title") or "Untitled",
                    "url": meta.get("url") or src.get("url") or "",
                    "content": self._extract_text(src.get("content") or src.get("snippet") or src.get("text")),
                }
            )
        return sources

    async def deep_search(self, query: str, source_mode: str, depth: str) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "error": "Vane disabled"}
        if not self.url:
            return {"enabled": False, "error": "Vane URL missing"}

        chat_provider_id = os.getenv(self.chat_provider_id_env, "")
        embed_provider_id = os.getenv(self.embedding_provider_id_env, "")
        if not chat_provider_id or not embed_provider_id:
            return {"enabled": False, "error": "Vane provider env values missing"}

        optimization_mode = self._optimization_mode(depth)
        timeout_s = self._timeout_for_depth(depth)
        source_map = {
            "web": ["web"],
            "academia": ["academic"],
            "social": ["discussions"],
            "all": ["web", "academic", "discussions"],
        }
        payload = {
            "query": query,
            "sources": source_map.get(source_mode, ["web"]),
            "optimizationMode": optimization_mode,
            "stream": False,
            "chatModel": {
                "providerId": chat_provider_id,
                "key": self.chat_model_key,
            },
            "embeddingModel": {
                "providerId": embed_provider_id,
                "key": self.embedding_model_key,
            },
        }

        try:
            logger.info(
                "event=vane_request query=%r source_mode=%s depth=%s optimization_mode=%s timeout_s=%s url=%s",
                query,
                source_mode,
                depth,
                optimization_mode,
                timeout_s,
                self.url,
            )
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(f"{self.url}/api/search", json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "event=vane_error query=%r status=%s optimization_mode=%s url=%s",
                    query,
                    resp.status_code,
                    optimization_mode,
                    self.url,
                )
                return {"enabled": True, "error": f"Vane HTTP {resp.status_code}", "sources": [], "optimization_mode": optimization_mode}
            body = resp.json()
            sources = self._extract_sources(body)
            answer = self._pick_first_text(
                body,
                [
                    ("answer",),
                    ("summary",),
                    ("message",),
                    ("data", "answer"),
                    ("data", "summary"),
                    ("data", "message"),
                    ("result", "answer"),
                    ("result", "summary"),
                    ("result", "message"),
                ],
            )
            summary = self._pick_first_text(
                body,
                [
                    ("summary",),
                    ("answer",),
                    ("message",),
                    ("data", "summary"),
                    ("data", "answer"),
                    ("result", "summary"),
                    ("result", "answer"),
                ],
            )
            logger.info(
                "event=vane_response query=%r source_count=%s has_answer=%s has_summary=%s url=%s",
                query,
                len(sources),
                bool(answer),
                bool(summary),
                self.url,
            )
            return {
                "enabled": True,
                "message": self._extract_text(body.get("message")),
                "answer": answer,
                "summary": summary,
                "sources": sources,
                "raw": body,
                "optimization_mode": optimization_mode,
                "timeout_s": timeout_s,
                "error": None,
            }
        except Exception as exc:
            logger.warning("event=vane_error query=%r error=%s optimization_mode=%s url=%s", query, exc, optimization_mode, self.url)
            return {
                "enabled": True,
                "error": str(exc),
                "sources": [],
                "optimization_mode": optimization_mode,
                "timeout_s": timeout_s,
            }
