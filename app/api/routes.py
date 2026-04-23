from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import redacted_config
from app.models.contracts import (
    ExtractRequest,
    FetchRequest,
    PerplexitySearchRequest,
    ResearchRequest,
    SearchRequest,
    SearxngCompatRequest,
)
from app.models.internal import SearchResponse
from app.services.orchestrator import SearchService
from app.services.research_proxy import ResearchProxyService
from app.services.searxng_compat import SearxngCompatService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_orchestrator(request: Request) -> SearchService:
    return request.app.state.orchestrator


def get_config(request: Request):
    return request.app.state.config


def get_router_health(request: Request):
    return request.app.state.provider_router.health_snapshot


def get_searxng_compat_service(request: Request) -> SearxngCompatService:
    return SearxngCompatService(
        config=request.app.state.config,
        orchestrator=request.app.state.orchestrator,
        router=request.app.state.provider_router,
    )


def get_research_proxy(request: Request) -> ResearchProxyService:
    return request.app.state.research_proxy


@router.post("/")
async def search_root(
    payload: PerplexitySearchRequest,
    orch: SearchService = Depends(get_orchestrator),
):
    """Root endpoint for Open WebUI Perplexity search integration"""
    try:
        response = await orch.execute_perplexity_search(payload)
    except ValueError as exc:
        query_text = payload.query if isinstance(payload.query, str) else "; ".join(payload.query)
        orch.record_failed_run(endpoint="/search", query=query_text, mode="fast", errors=[str(exc)])
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.model_dump(exclude_none=True)


@router.post("/search")
async def perplexity_search(
    payload: PerplexitySearchRequest,
    orch: SearchService = Depends(get_orchestrator),
):
    try:
        response = await orch.execute_perplexity_search(payload)
    except ValueError as exc:
        query_text = payload.query if isinstance(payload.query, str) else "; ".join(payload.query)
        orch.record_failed_run(endpoint="/search", query=query_text, mode="fast", errors=[str(exc)])
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.model_dump(exclude_none=True)


@router.post("/research")
async def research_search(
    payload: ResearchRequest,
    proxy: ResearchProxyService = Depends(get_research_proxy),
):
    return await proxy.streaming_response(payload)


async def _handle_searxng_compat_search(
    params: SearxngCompatRequest,
    compat: SearxngCompatService,
):
    return (await compat.execute(params)).model_dump(exclude_none=True)


@router.get("/compat/searxng")
async def searxng_compat_search(
    params: SearxngCompatRequest = Depends(),
    compat: SearxngCompatService = Depends(get_searxng_compat_service),
):
    return await _handle_searxng_compat_search(params, compat)


@router.get("/compat/searxng/search")
async def searxng_compat_search_vane(
    params: SearxngCompatRequest = Depends(),
    compat: SearxngCompatService = Depends(get_searxng_compat_service),
):
    return await _handle_searxng_compat_search(params, compat)


@router.post("/fetch")
async def fetch(payload: FetchRequest, orch: SearchService = Depends(get_orchestrator)):
    return await orch.fetch(payload.url)


@router.post("/extract")
async def extract(payload: ExtractRequest, orch: SearchService = Depends(get_orchestrator)):
    return await orch.extract(payload.url)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/providers/health")
async def provider_health(provider_health_state=Depends(get_router_health)):
    return {"providers": [item.model_dump() for item in provider_health_state()]}


@router.get("/config/effective")
async def effective_config(cfg=Depends(get_config)):
    return redacted_config(cfg)


@router.get("/metrics")
async def metrics(orch: SearchService = Depends(get_orchestrator)):
    return orch.metrics()


@router.get("/runs/recent")
async def recent_runs(limit: int = 20, orch: SearchService = Depends(get_orchestrator)):
    return {"runs": orch.recent_runs(limit=limit)}
