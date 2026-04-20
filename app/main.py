from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from app.services.vane import VaneClient

logger = logging.getLogger(__name__)


class Container:
    config: AppConfig
    orchestrator: ResearchOrchestrator
    provider_router: ProviderRouter


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

    vane = VaneClient(
        enabled=config.vane.enabled,
        url=config.vane.url,
        timeout_s=config.vane.timeout_s,
        default_optimization_mode=config.vane.default_optimization_mode,
        chat_provider_id_env=config.vane.chat_provider_id_env,
        chat_model_key=config.vane.chat_model_key,
        embedding_provider_id_env=config.vane.embedding_provider_id_env,
        embedding_model_key=config.vane.embedding_model_key,
    )

    return ResearchOrchestrator(
        config=config,
        router=router,
        search_cache=search_cache,
        page_cache=page_cache,
        fetcher=fetcher,
        planner=QueryPlanner(),
        ranker=Ranker(),
        vane=vane,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    configure_logging(config.logging.level, config.logging.json)

    router = _build_router(config)
    orchestrator = _build_orchestrator(config, router)

    container.config = config
    container.provider_router = router
    container.orchestrator = orchestrator
    app.state.config = config
    app.state.provider_router = router
    app.state.orchestrator = orchestrator

    enabled_providers = [provider.name for provider in config.providers if provider.enabled]
    disabled_providers = [provider.name for provider in config.providers if not provider.enabled]
    logger.info(
        "startup service=enhanced-websearch host=%s port=%s routing_policy=%s cooldown_seconds=%s failure_threshold=%s",
        config.service.host,
        config.service.port,
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
        "startup vane enabled=%s url=%s default_mode=%s chat_model_key=%s embedding_model_key=%s",
        config.vane.enabled,
        config.vane.url or "none",
        config.vane.default_optimization_mode,
        config.vane.chat_model_key,
        config.vane.embedding_model_key,
    )

    logger.info("Enhanced websearch service started")
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
    logger.info("Enhanced websearch service stopping")


app = FastAPI(title="Enhanced Websearch Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    OptionalBearerTokenMiddleware,
    bearer_token=os.getenv("EWS_BEARER_TOKEN", ""),
    exempt_paths=["/health", "/docs", "/redoc", "/openapi.json", "/mcp"],
)
app.include_router(routes.router)
app.mount("/mcp", mcp_app)
