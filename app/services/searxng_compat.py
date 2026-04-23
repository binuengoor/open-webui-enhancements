from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx

from app.core.config import AppConfig
from app.models.contracts import SearxngCompatRequest, SearxngCompatResponse, SearxngCompatResult
from app.providers.base import SearchProvider
from app.providers.router import ProviderRouter
from app.services.orchestrator import SearchService


logger = logging.getLogger(__name__)

_IMAGE_ENGINE_TOKENS = {
    "bing images",
    "google images",
    "images",
    "google image",
    "bing image",
}

_VIDEO_ENGINE_TOKENS = {
    "youtube",
    "videos",
    "video",
    "youtube videos",
}


@dataclass(frozen=True)
class CompatVerticalDecision:
    vertical: str
    passthrough_media: bool


class SearxngCompatService:
    def __init__(self, config: AppConfig, orchestrator: SearchService, router: ProviderRouter):
        self.config = config
        self.orchestrator = orchestrator
        self.router = router

    async def execute(self, req: SearxngCompatRequest) -> SearxngCompatResponse:
        decision = self._detect_vertical(req)
        if decision.passthrough_media:
            response = await self._passthrough_to_upstream(req)
            if response is not None:
                return response
            return self._empty_payload(req.q, unresponsive_engines=["searxng"])

        return await self._web_compat_search(req)

    def _detect_vertical(self, req: SearxngCompatRequest) -> CompatVerticalDecision:
        tokens = {token.lower() for token in req.categories_list + req.engines_list}
        if tokens & _IMAGE_ENGINE_TOKENS:
            return CompatVerticalDecision(vertical="images", passthrough_media=True)
        if tokens & _VIDEO_ENGINE_TOKENS:
            return CompatVerticalDecision(vertical="videos", passthrough_media=True)
        return CompatVerticalDecision(vertical="web", passthrough_media=False)

    async def _web_compat_search(self, req: SearxngCompatRequest) -> SearxngCompatResponse:
        rows, trace, _cache_meta = await self.orchestrator.search_rows(
            query=req.q,
            mode="fast",
            source_mode="web",
            depth="balanced",
            max_attempts=self.config.search_limits.max_provider_attempts,
            request_id=f"compat-{req.pageno}",
            limit_override=None,
            extra_options={
                "categories": self._categories_value(req),
                "language": req.language,
                "time_range": req.time_range,
                "pageno": req.pageno,
            },
        )
        results = [self._map_web_result(item) for item in rows]
        return SearxngCompatResponse(
            query=req.q,
            number_of_results=len(results),
            results=results,
            answers=[],
            corrections=[],
            infoboxes=[],
            suggestions=[],
            unresponsive_engines=self._unresponsive_engines(trace),
        )

    async def _passthrough_to_upstream(self, req: SearxngCompatRequest) -> SearxngCompatResponse | None:
        provider = self._find_searxng_provider()
        if provider is None:
            return None

        params = {
            "q": req.q,
            "format": "json",
            "categories": req.categories,
            "engines": req.engines,
            "language": req.language,
            "pageno": req.pageno,
            "time_range": req.time_range,
        }
        params = {key: value for key, value in params.items() if value not in (None, "")}
        url = f"{provider.base_url}/search"

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_s) as client:
                response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("event=searxng_compat_passthrough_failed url=%s error=%s", url, exc)
            return None

        return SearxngCompatResponse.model_validate(
            {
                "query": payload.get("query", req.q),
                "number_of_results": payload.get("number_of_results", len(payload.get("results", []))),
                "results": payload.get("results", []),
                "answers": payload.get("answers", []),
                "corrections": payload.get("corrections", []),
                "infoboxes": payload.get("infoboxes", []),
                "suggestions": payload.get("suggestions", []),
                "unresponsive_engines": payload.get("unresponsive_engines", []),
            }
        )

    def _find_searxng_provider(self) -> SearchProvider | None:
        return self.router.get_provider("searxng")

    def _map_web_result(self, item: Dict[str, Any]) -> SearxngCompatResult:
        return SearxngCompatResult(
            title=item.get("title", "Untitled"),
            url=item.get("url", ""),
            content=item.get("snippet", ""),
            engine=item.get("provider"),
        )

    def _unresponsive_engines(self, trace: List[Dict[str, Any]]) -> List[str]:
        unresponsive: List[str] = []
        for entry in trace:
            status = entry.get("status")
            provider = entry.get("provider")
            if status in {"rate_limit", "transient", "upstream", "provider_error", "auth"} and provider:
                unresponsive.append(str(provider))
        return unresponsive

    def _categories_value(self, req: SearxngCompatRequest) -> str:
        if req.categories:
            return req.categories
        return "general"

    def _empty_payload(self, query: str, unresponsive_engines: List[str] | None = None) -> SearxngCompatResponse:
        return SearxngCompatResponse(
            query=query,
            number_of_results=0,
            results=[],
            answers=[],
            corrections=[],
            infoboxes=[],
            suggestions=[],
            unresponsive_engines=unresponsive_engines or [],
        )
