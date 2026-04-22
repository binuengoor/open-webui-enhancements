from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

import httpx

from app.providers.base import (
    AuthProviderError,
    ProviderError,
    RateLimitError,
    SearchProvider,
    TransientProviderError,
    UpstreamProviderError,
)


logger = logging.getLogger(__name__)


class LiteLLMSearchProvider(SearchProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        path: str,
        timeout_s: int,
        api_key_env: str | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.timeout_s = timeout_s
        self.api_key_env = api_key_env

    async def search(self, query: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        request_id = options.get("request_id", "-")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key_env:
            token = os.getenv(self.api_key_env, "")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        payload = {
            "query": query,
            "k": options.get("limit", 8),
            "source_mode": options.get("source_mode", "web"),
            "depth": options.get("depth", "balanced"),
        }

        url = f"{self.base_url}{self.path}"
        started = time.perf_counter()
        logger.info("event=provider_http_request request_id=%s provider=%s url=%s query=%r", request_id, self.name, url, query)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning(
                "event=provider_http_exception request_id=%s provider=%s error_type=%s error=%r",
                request_id,
                self.name,
                type(exc).__name__,
                exc,
            )
            raise TransientProviderError(f"{self.name} request timed out") from exc
        except httpx.NetworkError as exc:
            logger.warning(
                "event=provider_http_exception request_id=%s provider=%s error_type=%s error=%r",
                request_id,
                self.name,
                type(exc).__name__,
                exc,
            )
            raise TransientProviderError(f"{self.name} network error: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "event=provider_http_exception request_id=%s provider=%s error_type=%s error=%r",
                request_id,
                self.name,
                type(exc).__name__,
                exc,
            )
            raise ProviderError(f"{self.name} request failed: {type(exc).__name__}") from exc

        if resp.status_code == 429:
            logger.warning("event=provider_http_error request_id=%s provider=%s status=429 query=%r", request_id, self.name, query)
            retry_after = resp.headers.get("retry-after", "").strip()
            cooldown_seconds = int(retry_after) if retry_after.isdigit() else None
            raise RateLimitError(f"{self.name} rate limited", cooldown_seconds=cooldown_seconds)
        if resp.status_code in {401, 403}:
            logger.warning("event=provider_http_error request_id=%s provider=%s status=%s query=%r", request_id, self.name, resp.status_code, query)
            raise AuthProviderError(f"{self.name} auth failed with HTTP {resp.status_code}")
        if resp.status_code in {502, 503, 504}:
            logger.warning("event=provider_http_error request_id=%s provider=%s status=%s query=%r", request_id, self.name, resp.status_code, query)
            raise UpstreamProviderError(f"{self.name} upstream HTTP {resp.status_code}")
        if resp.status_code >= 500:
            logger.warning("event=provider_http_error request_id=%s provider=%s status=%s query=%r", request_id, self.name, resp.status_code, query)
            raise UpstreamProviderError(f"{self.name} HTTP {resp.status_code}")
        if resp.status_code >= 400:
            logger.warning("event=provider_http_error request_id=%s provider=%s status=%s query=%r", request_id, self.name, resp.status_code, query)
            raise ProviderError(f"{self.name} HTTP {resp.status_code}")

        raw = resp.json()
        rows = raw.get("results") or raw.get("data") or []

        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(rows, start=1):
            normalized.append(
                {
                    "url": item.get("url", ""),
                    "title": item.get("title", "Untitled"),
                    "snippet": item.get("snippet", item.get("content", "")),
                    "source": item.get("source", "web"),
                    "provider": self.name,
                    "rank": idx,
                }
            )
        logger.info(
            "event=provider_http_response request_id=%s provider=%s status=%s result_count=%s latency_ms=%s",
            request_id,
            self.name,
            resp.status_code,
            len(normalized),
            int((time.perf_counter() - started) * 1000),
        )
        return normalized
