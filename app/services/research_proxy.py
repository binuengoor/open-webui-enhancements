from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from fastapi import HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.core.config import AppConfig
from app.models.contracts import ResearchRequest
from app.services.run_history import RecentRunHistory

logger = logging.getLogger(__name__)


def _optimization_mode(depth: str) -> str:
    if depth == "quick":
        return "speed"
    if depth == "quality":
        return "quality"
    return "balanced"


def _source_list(source_mode: str) -> list[str]:
    mapping = {
        "web": ["web"],
        "academia": ["academic"],
        "social": ["discussions"],
        "all": ["web", "academic", "discussions"],
    }
    return mapping.get(source_mode, ["web"])


@dataclass
class UpstreamStream:
    status_code: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]
    background: BackgroundTask


class ResearchProxyService:
    def __init__(self, config: AppConfig, run_history: RecentRunHistory | None = None):
        self.config = config
        self.run_history = run_history or RecentRunHistory()

    def ensure_ready(self) -> None:
        if not self.config.research_llm_ready:
            raise HTTPException(status_code=400, detail=self.config.research_llm_requirement_error)

    def build_upstream_payload(self, payload: ResearchRequest) -> dict:
        vane = self.config.vane
        return {
            "chatModel": {
                "providerId": vane.chat_provider_id,
                "key": vane.chat_model_key,
            },
            "embeddingModel": {
                "providerId": vane.embedding_provider_id,
                "key": vane.embedding_model_key,
            },
            "optimizationMode": _optimization_mode(payload.depth),
            "sources": _source_list(payload.source_mode),
            "query": payload.query,
            "history": payload.history,
            "systemInstructions": payload.system_instructions,
            "stream": True,
        }

    async def stream_research(self, payload: ResearchRequest) -> UpstreamStream:
        self.ensure_ready()

        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        upstream_payload = self.build_upstream_payload(payload)
        url = f"{self.config.vane.url.rstrip('/')}/api/search"

        logger.info(
            "event=research_proxy_start request_id=%s query=%r source_mode=%s depth=%s upstream_url=%s",
            request_id,
            payload.query,
            payload.source_mode,
            payload.depth,
            url,
        )

        client = httpx.AsyncClient(timeout=self.config.vane.timeout_s)
        try:
            response = await client.send(
                client.build_request("POST", url, json=upstream_payload),
                stream=True,
            )
        except httpx.TimeoutException as exc:
            await client.aclose()
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(
                "event=research_proxy_timeout request_id=%s query=%r latency_ms=%s upstream_url=%s",
                request_id,
                payload.query,
                latency_ms,
                url,
            )
            raise HTTPException(status_code=504, detail="Vane research request timed out") from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(
                "event=research_proxy_transport_error request_id=%s query=%r latency_ms=%s upstream_url=%s error=%s",
                request_id,
                payload.query,
                latency_ms,
                url,
                exc,
            )
            raise HTTPException(status_code=502, detail="Vane research request failed") from exc

        if response.status_code >= 400:
            body_text = await response.aread()
            await response.aclose()
            await client.aclose()
            latency_ms = int((time.perf_counter() - started) * 1000)
            detail = self._upstream_error_detail(body_text, response.status_code)
            logger.warning(
                "event=research_proxy_upstream_error request_id=%s query=%r latency_ms=%s status=%s detail=%r",
                request_id,
                payload.query,
                latency_ms,
                response.status_code,
                detail,
            )
            raise HTTPException(status_code=response.status_code, detail=detail)

        headers = self._passthrough_headers(response.headers)
        logger.info(
            "event=research_proxy_connected request_id=%s query=%r status=%s latency_ms=%s content_type=%r",
            request_id,
            payload.query,
            response.status_code,
            int((time.perf_counter() - started) * 1000),
            headers.get("content-type", ""),
        )

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    if chunk:
                        yield chunk
            finally:
                latency_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "event=research_proxy_complete request_id=%s query=%r status=%s latency_ms=%s",
                    request_id,
                    payload.query,
                    response.status_code,
                    latency_ms,
                )

        background = BackgroundTask(self._close_upstream, response, client)
        return UpstreamStream(
            status_code=response.status_code,
            headers=headers,
            body=body_iter(),
            background=background,
        )

    async def streaming_response(self, payload: ResearchRequest) -> StreamingResponse:
        upstream = await self.stream_research(payload)
        return StreamingResponse(
            upstream.body,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=upstream.headers,
            background=upstream.background,
        )

    def _passthrough_headers(self, headers: httpx.Headers) -> dict[str, str]:
        allowed = {
            "cache-control",
            "connection",
            "content-type",
            "x-request-id",
            "x-trace-id",
            "retry-after",
            "warning",
        }
        out: dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if lowered in allowed:
                out[lowered] = value
        out.setdefault("cache-control", "no-cache")
        out.setdefault("connection", "keep-alive")
        out.setdefault("content-type", "text/event-stream")
        return out

    def _upstream_error_detail(self, body: bytes, status_code: int) -> str:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return f"Vane returned HTTP {status_code}"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text[:500]

        if isinstance(parsed, dict):
            for key in ("detail", "error", "message"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = value.get("message") or value.get("detail")
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        return text[:500]

    async def _close_upstream(self, response: httpx.Response, client: httpx.AsyncClient) -> None:
        await response.aclose()
        await client.aclose()
