from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

import httpx
try:
    from mcp.server.fastmcp import Context, FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("The 'mcp' package is required to run the MCP server") from exc


logger = logging.getLogger(__name__)


@dataclass
class MCPConfig:
    backend_url: str
    request_timeout_s: int
    default_mode: str
    bearer_token: str


@dataclass
class MCPContext:
    config: MCPConfig
    client: httpx.AsyncClient


def _load_mcp_config() -> MCPConfig:
    return MCPConfig(
        backend_url=os.getenv("EWS_MCP_BACKEND_URL", os.getenv("EWS_SERVICE_BASE_URL", "http://enhanced-websearch:8091")),
        request_timeout_s=int(os.getenv("EWS_MCP_REQUEST_TIMEOUT", os.getenv("EWS_REQUEST_TIMEOUT", "25"))),
        default_mode=os.getenv("EWS_MCP_DEFAULT_MODE", "auto"),
        bearer_token=os.getenv("EWS_BEARER_TOKEN", ""),
    )


@asynccontextmanager
async def lifespan(_: FastMCP):
    config = _load_mcp_config()
    async with httpx.AsyncClient(
        base_url=config.backend_url.rstrip("/"),
        timeout=config.request_timeout_s,
        headers={
            **({"Authorization": f"Bearer {config.bearer_token}"} if config.bearer_token else {}),
            "Content-Type": "application/json",
        },
    ) as client:
        yield MCPContext(config=config, client=client)


mcp = FastMCP(
    "Enhanced Websearch MCP",
    json_response=True,
    stateless_http=True,
    instructions=(
        "Use this server for search, page fetches, structured extraction, and provider health. "
        "The search tool returns the rich research payload from the backend; the perplexity_search "
        "tool returns Perplexity-style results for drop-in compatibility."
    ),
    lifespan=lifespan,
)
mcp.settings.streamable_http_path = "/"


async def _backend_post(ctx: Context, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await ctx.request_context.lifespan_context.client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


async def _backend_get(ctx: Context, path: str) -> dict[str, Any]:
    response = await ctx.request_context.lifespan_context.client.get(path)
    response.raise_for_status()
    return response.json()


def _require_ctx(ctx: Context | None) -> Context:
    if ctx is None:
        raise ValueError("MCP request context is not available")
    return ctx


@mcp.tool()
async def search(
    query: str,
    mode: str = "auto",
    source_mode: str = "web",
    depth: str = "balanced",
    max_iterations: int = 4,
    include_citations: bool = True,
    include_legacy: bool = False,
    strict_runtime: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run the full research backend and return the rich result payload."""
    ctx = _require_ctx(ctx)
    effective_mode = mode if mode != "auto" else ctx.request_context.lifespan_context.config.default_mode
    payload = {
        "query": query,
        "mode": effective_mode,
        "source_mode": source_mode,
        "depth": depth,
        "max_iterations": max_iterations,
        "include_citations": include_citations,
        "include_debug": False,
        "include_legacy": include_legacy,
        "strict_runtime": strict_runtime,
        "user_context": {"client": "mcp", "tool": "search"},
    }
    return await _backend_post(ctx, "/internal/search", payload)


@mcp.tool()
async def perplexity_search(
    query: str | list[str],
    max_results: int = 10,
    display_server_time: bool = False,
    country: Optional[str] = None,
    max_tokens: Optional[int] = None,
    max_tokens_per_page: Optional[int] = None,
    search_language_filter: Optional[list[str]] = None,
    search_domain_filter: Optional[list[str]] = None,
    search_recency_filter: Optional[str] = None,
    search_after_date_filter: Optional[str] = None,
    search_before_date_filter: Optional[str] = None,
    last_updated_after_filter: Optional[str] = None,
    last_updated_before_filter: Optional[str] = None,
    search_mode: Optional[str] = None,
    mode: Optional[str] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Return Perplexity-style search results."""
    ctx = _require_ctx(ctx)
    effective_mode = mode or ctx.request_context.lifespan_context.config.default_mode
    payload = {
        "query": query,
        "max_results": max_results,
        "display_server_time": display_server_time,
        "country": country,
        "max_tokens": max_tokens,
        "max_tokens_per_page": max_tokens_per_page,
        "search_language_filter": search_language_filter,
        "search_domain_filter": search_domain_filter,
        "search_recency_filter": search_recency_filter,
        "search_after_date_filter": search_after_date_filter,
        "search_before_date_filter": search_before_date_filter,
        "last_updated_after_filter": last_updated_after_filter,
        "last_updated_before_filter": last_updated_before_filter,
        "search_mode": search_mode,
        "mode": effective_mode,
        "client": "mcp",
    }
    return await _backend_post(ctx, "/search", payload)


@mcp.tool()
async def fetch_page(url: str, ctx: Context | None = None) -> dict[str, Any]:
    """Fetch and extract a single page."""
    ctx = _require_ctx(ctx)
    return await _backend_post(ctx, "/fetch", {"url": url})


@mcp.tool()
async def extract_page_structure(url: str, components: str = "all", ctx: Context | None = None) -> dict[str, Any]:
    """Extract page structure and metadata."""
    ctx = _require_ctx(ctx)
    return await _backend_post(ctx, "/extract", {"url": url, "components": components})


@mcp.tool()
async def health_check(ctx: Context | None = None) -> dict[str, Any]:
    """Check service health."""
    ctx = _require_ctx(ctx)
    return await _backend_get(ctx, "/health")


@mcp.tool()
async def providers_health(ctx: Context | None = None) -> dict[str, Any]:
    """Inspect provider health and cooldown state."""
    ctx = _require_ctx(ctx)
    return await _backend_get(ctx, "/providers/health")


app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("EWS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("EWS_MCP_PORT", "8092"))
    uvicorn.run(app, host=host, port=port)