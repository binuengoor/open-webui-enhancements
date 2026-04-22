from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import redacted_config
from app.models.contracts import (
    ExtractRequest,
    FetchRequest,
    PerplexitySearchRequest,
    ProgressEvent,
    ResearchExportRequest,
    ResearchRequest,
    SearchRequest,
    SearxngCompatRequest,
)
from app.services.orchestrator import ResearchOrchestrator
from app.services.report_exporter import ReportExporter
from app.services.searxng_compat import SearxngCompatService

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse_event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def get_orchestrator(request: Request) -> ResearchOrchestrator:
    return request.app.state.orchestrator


def get_config(request: Request):
    return request.app.state.config


def get_router_health(request: Request):
    return request.app.state.provider_router.health_snapshot


def get_report_exporter(request: Request) -> ReportExporter:
    return request.app.state.report_exporter


def get_searxng_compat_service(request: Request) -> SearxngCompatService:
    return SearxngCompatService(
        config=request.app.state.config,
        orchestrator=request.app.state.orchestrator,
        router=request.app.state.provider_router,
    )


@router.post("/")
async def search_root(
    payload: PerplexitySearchRequest,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
):
    """Root endpoint for Open WebUI Perplexity search integration"""
    try:
        response = await orch.execute_perplexity_search(payload)
    except ValueError as exc:
        query_text = payload.query if isinstance(payload.query, str) else "; ".join(payload.query)
        orch.record_failed_run(endpoint="/search", query=query_text, mode="fast", errors=[str(exc)])
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.model_dump(exclude_none=True)


@router.post("/internal/search")
async def search(payload: SearchRequest, orch: ResearchOrchestrator = Depends(get_orchestrator)):
    response = await orch.execute_search(payload, endpoint="/research")
    return response.model_dump()


@router.post("/search")
async def perplexity_search(
    payload: PerplexitySearchRequest,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
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
    request: Request,
    orch: ResearchOrchestrator = Depends(get_orchestrator),
):
    # `/research` is the explicitly LLM-backed long-form path.
    try:
        orch.ensure_research_llm_available()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        try:
            response = await orch.execute_search(internal_request, endpoint="/research")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return response.model_dump()

    # Pre-generate request id so error events carry proper identity
    request_id = uuid.uuid4().hex[:8]

    async def event_stream():
        queue: asyncio.Queue[ProgressEvent | dict | None] = asyncio.Queue()

        async def on_progress(event: ProgressEvent) -> None:
            await queue.put(event)

        async def run_search() -> None:
            try:
                response = await orch.execute_search(internal_request, progress_callback=on_progress, endpoint="/research")
                await queue.put({"type": "result", "payload": response.model_dump()})
            except Exception as exc:
                await queue.put(
                    ProgressEvent(
                        type="error",
                        state="error",
                        request_id=request_id,
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


@router.get("/compat/searxng")
async def searxng_compat_search(
    params: SearxngCompatRequest = Depends(),
    compat: SearxngCompatService = Depends(get_searxng_compat_service),
):
    return (await compat.execute(params)).model_dump(exclude_none=True)


@router.post("/research/export")
async def export_research_report(
    payload: ResearchExportRequest,
    exporter: ReportExporter = Depends(get_report_exporter),
):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, exporter.export_research_report, payload.response)
    except ValueError as exc:
        logger.warning("research export failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.get("/runs/recent")
async def recent_runs(limit: int = 20, orch: ResearchOrchestrator = Depends(get_orchestrator)):
    return {"runs": orch.recent_runs(limit=limit)}
