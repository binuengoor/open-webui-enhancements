from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import routes
from app.cache.memory_cache import InMemoryCache
from app.core.config import AppConfig, load_config
from app.core.auth import OptionalBearerTokenMiddleware
from app.core.logging_utils import configure_logging
from app.mcp_server import app as mcp_app
from app.providers.litellm_search import LiteLLMSearchProvider
from app.providers.router import ProviderRouter, ProviderSlot
from app.providers.searxng import SearxngProvider
from app.services.fetcher import PageFetcher
from app.services.orchestrator import ResearchOrchestrator
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker
from app.services.run_history import RecentRunHistory
from app.services.research_proxy import ResearchProxyService

logger = logging.getLogger(__name__)


class Container:
    config: AppConfig
    orchestrator: ResearchOrchestrator
    provider_router: ProviderRouter
    research_proxy: ResearchProxyService


container = Container()


def _build_router(config: AppConfig) -> ProviderRouter:
    slots = []
    for provider in config.providers:
        if provider.kind == "searxng":
            impl = SearxngProvider(provider.name, provider.base_url, provider.timeout_s)
        elif provider.kind == "litellm-search":
            impl = LiteLLMSearchProvider(
                name=provider.name,
                base_url=provider.base_url,
                path=provider.path,
                timeout_s=provider.timeout_s,
                api_key_env=provider.api_key_env,
            )
        else:
            logger.warning("Skipping unknown provider kind=%s name=%s", provider.kind, provider.name)
            continue
        slots.append(ProviderSlot(provider=impl, weight=provider.weight, enabled=provider.enabled))

    return ProviderRouter(
        slots=slots,
        cooldown_seconds=config.routing.cooldown_seconds,
        failure_threshold=config.routing.failure_threshold,
    )


def _build_orchestrator(config: AppConfig, router: ProviderRouter) -> ResearchOrchestrator:
    search_cache = InMemoryCache(config.cache.max_entries)
    page_cache = InMemoryCache(config.cache.max_entries)

    fetcher = PageFetcher(
        timeout_s=config.scraping.request_timeout_s,
        max_chars=config.scraping.max_content_chars,
        flaresolverr_url=config.scraping.flaresolverr_url,
        user_agent=config.scraping.user_agent,
    )

    return ResearchOrchestrator(
        config=config,
        router=router,
        search_cache=search_cache,
        page_cache=page_cache,
        fetcher=fetcher,
        planner=QueryPlanner(),
        ranker=Ranker(),
        run_history=RecentRunHistory(max_entries=100),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    configure_logging(config.logging.level, config.logging.json)

    router = _build_router(config)
    orchestrator = _build_orchestrator(config, router)

    container.config = config
    research_proxy = ResearchProxyService(config=config)

    container.provider_router = router
    container.orchestrator = orchestrator
    container.research_proxy = research_proxy
    app.state.config = config
    app.state.provider_router = router
    app.state.orchestrator = orchestrator
    app.state.research_proxy = research_proxy

    enabled_providers = [provider.name for provider in config.providers if provider.enabled]
    disabled_providers = [provider.name for provider in config.providers if not provider.enabled]
    logger.info(
        "startup service=enhanced-websearch routing_policy=%s cooldown_seconds=%s failure_threshold=%s",
        config.routing.policy,
        config.routing.cooldown_seconds,
        config.routing.failure_threshold,
    )
    logger.info(
        "startup active_providers=%s disabled_providers=%s cache_enabled=%s search_ttl=%s recency_ttl=%s",
        ",".join(enabled_providers) if enabled_providers else "none",
        ",".join(disabled_providers) if disabled_providers else "none",
        config.cache.enabled,
        config.cache.ttl_general_s,
        config.cache.ttl_recency_s,
    )
    logger.info(
        "startup vane enabled=%s url=%s chat_provider_id_set=%s embedding_provider_id_set=%s chat_model_key=%s embedding_model_key=%s",
        config.vane.enabled,
        config.vane.url or "none",
        bool(config.vane.chat_provider_id),
        bool(config.vane.embedding_provider_id),
        config.vane.chat_model_key,
        config.vane.embedding_model_key,
    )
    logger.info(
        "startup research_llm_ready=%s",
        config.research_llm_ready,
    )

    logger.info("Enhanced websearch service started")
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
    logger.info("Enhanced websearch service stopping")


app = FastAPI(title="Enhanced Websearch Service", version="0.1.0", lifespan=lifespan)
auth_enabled = os.getenv("EWS_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
auth_token = os.getenv("EWS_AUTH_TOKEN", "") if auth_enabled else ""
app.add_middleware(
    OptionalBearerTokenMiddleware,
    bearer_token=auth_token,
    exempt_paths=["/health", "/docs", "/redoc", "/openapi.json", "/mcp"],
)
app.include_router(routes.router)
app.mount("/mcp", mcp_app)


def _perplexity_error(status_code: int, error_type: str, message: str, param: str | None = None) -> JSONResponse:
    body = {"error": {"type": error_type, "message": message}}
    if param:
        body["error"]["param"] = param
    return JSONResponse(body, status_code=status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/search":
        first_error = exc.errors()[0] if exc.errors() else {}
        loc = first_error.get("loc", [])
        param = loc[-1] if isinstance(loc, list) and loc else None
        message = first_error.get("msg", "Invalid request")
        return _perplexity_error(400, "invalid_request_error", message, str(param) if param else None)
    return JSONResponse({"detail": exc.errors()}, status_code=422)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path == "/search":
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if exc.status_code == 401:
            return _perplexity_error(401, "authentication_error", detail)
        if exc.status_code == 403:
            return _perplexity_error(403, "permission_error", detail)
        if exc.status_code == 429:
            return _perplexity_error(429, "rate_limit_error", detail)
        if 400 <= exc.status_code < 500:
            return _perplexity_error(exc.status_code, "invalid_request_error", detail)
        return _perplexity_error(exc.status_code, "internal_error", detail)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if request.url.path == "/search":
        logger.exception("Unhandled /search error: %s", exc)
        return _perplexity_error(500, "internal_error", "Internal server error")
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)
