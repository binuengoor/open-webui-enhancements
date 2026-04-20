from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import redacted_config
from app.models.contracts import (
    ExtractRequest,
    FetchRequest,
    PerplexitySearchRequest,
    SearchRequest,
)
from app.services.orchestrator import ResearchOrchestrator

router = APIRouter()

def get_orchestrator(request: Request) -> ResearchOrchestrator:
    return request.app.state.orchestrator


def get_config(request: Request):
    return request.app.state.config


def get_router_health(request: Request):
    return request.app.state.provider_router.health_snapshot


@router.post("/internal/search")
async def search(payload: SearchRequest, orch: ResearchOrchestrator = Depends(get_orchestrator)):
    response = await orch.execute_search(payload)
    return response.model_dump()


@router.post("/search")
async def perplexity_search(
    payload: PerplexitySearchRequest,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
):
    try:
        response = await orch.execute_perplexity_search(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.model_dump(exclude_none=True)


@router.post("/fetch")
async def fetch(payload: FetchRequest, orch: ResearchOrchestrator = Depends(get_orchestrator)):
    return await orch.fetch(payload.url)


@router.post("/extract")
async def extract(payload: ExtractRequest, orch: ResearchOrchestrator = Depends(get_orchestrator)):
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
async def metrics(orch: ResearchOrchestrator = Depends(get_orchestrator)):
    return orch.metrics()
