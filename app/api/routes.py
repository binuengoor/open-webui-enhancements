from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import redacted_config
from app.models.contracts import (
    ExtractRequest,
    FetchRequest,
    PerplexitySearchRequest,
    ProgressEvent,
    ResearchRequest,
    SearchRequest,
)
from app.services.orchestrator import ResearchOrchestrator

router = APIRouter()


def _sse_event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def get_orchestrator(request: Request) -> ResearchOrchestrator:
    return request.app.state.orchestrator


def get_config(request: Request):
    return request.app.state.config


def get_router_health(request: Request):
    return request.app.state.provider_router.health_snapshot


@router.post("/")
async def search_root(
    payload: PerplexitySearchRequest,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
):
    """Root endpoint for Open WebUI Perplexity search integration"""
    try:
        response = await orch.execute_perplexity_search(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.model_dump(exclude_none=True)


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


@router.post("/research")
async def research_search(
    payload: ResearchRequest,
    request: Request,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
):
    internal_request = SearchRequest(
        query=payload.query,
        mode="research",
        source_mode=payload.source_mode,
        depth=payload.depth,
        max_iterations=payload.max_iterations,
        include_citations=True,
        include_debug=payload.include_debug,
        include_legacy=payload.include_legacy,
        strict_runtime=payload.strict_runtime,
        user_context=payload.user_context,
    )

    stream_progress = request.query_params.get("stream") == "true"
    if not stream_progress:
        response = await orch.execute_search(internal_request)
        return response.model_dump()

    async def event_stream():
        queue: asyncio.Queue[ProgressEvent | dict | None] = asyncio.Queue()

        async def on_progress(event: ProgressEvent) -> None:
            await queue.put(event)

        async def run_search() -> None:
            try:
                response = await orch.execute_search(internal_request, progress_callback=on_progress)
                await queue.put({"type": "result", "payload": response.model_dump()})
            except Exception as exc:
                await queue.put(
                    ProgressEvent(
                        type="error",
                        state="error",
                        request_id="unknown",
                        mode="research",
                        error=str(exc),
                        message="Search failed",
                    )
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_search())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, ProgressEvent):
                    yield _sse_event(item.type, item.model_dump(exclude_none=True))
                else:
                    yield _sse_event(item["type"], item["payload"])
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


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
