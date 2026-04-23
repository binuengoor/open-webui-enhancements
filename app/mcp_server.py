from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import httpx
try:
    from mcp.server.fastmcp import Context, FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("The 'mcp' package is required to run the MCP server") from exc


logger = logging.getLogger(__name__)


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_allowed_host(value: str) -> list[str]:
    token = value.strip()
    if not token:
        return []
    if token == "*":
        return [token]

    # Accept URL-like values in env and extract hostname.
    token = re.sub(r"^https?://", "", token, flags=re.IGNORECASE)
    token = token.split("/", 1)[0]

    # Keep host:* entries as-is, but also allow the bare host because some
    # clients send Host without a port while others include host:port.
    if token.endswith(":*"):
        bare_host = token[:-2]
        if bare_host:
            return [bare_host, token]
        return [token]

    # Preserve explicit host:port entries and IPv6 bracket forms as-is.
    if token.startswith("[") or token.count(":") >= 1:
        return [token]

    # Convert bare hosts to both the bare host and a wildcard-port entry so
    # common MCP clients that send host or host:port headers will still match.
    return [token, f"{token}:*"]


def _normalized_allowed_hosts(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for norm in _normalize_allowed_host(raw):
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
    return out


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
        bearer_token=(os.getenv("EWS_AUTH_TOKEN", "") if os.getenv("EWS_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"} else ""),
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
        "The research tool proxies Vane's streamed research output as-is; "
        "the search tool returns concise results."
    ),
    lifespan=lifespan,
)
mcp.settings.streamable_http_path = "/"

# FastMCP transport_security nested env parsing for list fields can be unreliable
# across runtimes. Apply explicit env-driven overrides here for predictable behavior.
_allowed_hosts: list[str] = []
_allowed_origins: list[str] = []
_dns_rebinding = None

if mcp.settings.transport_security is not None:
    normalized_hosts = _normalized_allowed_hosts(_allowed_hosts)
    if normalized_hosts:
        mcp.settings.transport_security.allowed_hosts = normalized_hosts
    else:
        # FastMCP compares against Host headers, so allow both bare and
        # wildcard-port forms for common local/LAN deployments when an explicit
        # allowlist is not set.
        mcp.settings.transport_security.allowed_hosts = [
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
            "[::1]",
            "[::1]:*",
            "enhanced-websearch",
            "enhanced-websearch:*",
            "10.1.1.150",
            "10.1.1.150:*",
            "192.168.16.1",
            "192.168.16.1:*",
        ]
    if _allowed_origins:
        mcp.settings.transport_security.allowed_origins = _allowed_origins
    if _dns_rebinding is not None:
        mcp.settings.transport_security.enable_dns_rebinding_protection = _dns_rebinding.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


async def _backend_post(ctx: Context, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = await ctx.request_context.lifespan_context.client.post(path, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        timeout_s = ctx.request_context.lifespan_context.config.request_timeout_s
        raise RuntimeError(
            f"backend request to {path} timed out after {timeout_s}s; increase EWS_MCP_REQUEST_TIMEOUT for long-running tools"
        ) from exc


async def _backend_get(ctx: Context, path: str) -> dict[str, Any]:
    response = await ctx.request_context.lifespan_context.client.get(path)
    response.raise_for_status()
    return response.json()


@mcp.tool()
async def research(
    query: str,
    source_mode: Literal["web", "academia", "social", "all"] = "web",
    depth: Literal["quick", "balanced", "quality"] = "quality",
    history: Optional[list[dict[str, Any]]] = None,
    system_instructions: str = "",
    ctx: Context = None,
) -> dict[str, Any]:
    """Proxy Vane-backed research via /research.

    This mirrors the Vane relay surface: query, sources, optimization depth,
    optional history, and optional system instructions.
    """
    payload = {
        "query": query,
        "source_mode": source_mode,
        "depth": depth,
        "history": history or [],
        "system_instructions": system_instructions,
        "user_context": {"client": "mcp", "tool": "research"},
    }
    return await _backend_post(ctx, "/research", payload)


@mcp.tool()
async def search(
    query: str | list[str],
    max_results: int = 10,
    display_server_time: bool = False,
    search_recency_filter: Literal["none", "hour", "day", "week", "month", "year"] = "none",
    search_recency_amount: int = 1,
    search_mode: Literal["auto", "web", "academic", "sec"] = "auto",
    country: Optional[str] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Return Perplexity-style search results."""
    if search_recency_amount < 1:
        raise ValueError("search_recency_amount must be >= 1")

    normalized_recency_filter = None if search_recency_filter == "none" else search_recency_filter
    normalized_search_mode = None if search_mode == "auto" else search_mode

    effective_recency_filter = normalized_recency_filter
    search_after_date_filter = None
    if normalized_recency_filter and search_recency_amount > 1:
        now = datetime.now(timezone.utc)
        if normalized_recency_filter == "hour":
            cutoff = now - timedelta(hours=search_recency_amount)
        elif normalized_recency_filter == "day":
            cutoff = now - timedelta(days=search_recency_amount)
        elif normalized_recency_filter == "week":
            cutoff = now - timedelta(days=7 * search_recency_amount)
        elif normalized_recency_filter == "month":
            cutoff = now - timedelta(days=31 * search_recency_amount)
        else:  # year
            cutoff = now - timedelta(days=366 * search_recency_amount)

        # Use explicit after-date for multi-unit windows (e.g., 3 months).
        # Disable fixed recency buckets to avoid unintentionally narrowing to 1 unit.
        search_after_date_filter = cutoff.isoformat()
        effective_recency_filter = None

    payload = {
        "query": query,
        "max_results": max_results,
        "display_server_time": display_server_time,
        "country": country,
        "search_recency_filter": effective_recency_filter,
        "search_after_date_filter": search_after_date_filter,
        "search_mode": normalized_search_mode,
        "client": "mcp",
    }
    return await _backend_post(ctx, "/search", payload)


@mcp.tool()
async def fetch_page(url: str, ctx: Context = None) -> dict[str, Any]:
    """Fetch and extract a single page."""
    return await _backend_post(ctx, "/fetch", {"url": url})


@mcp.tool()
async def extract_page_structure(url: str, components: str = "all", ctx: Context = None) -> dict[str, Any]:
    """Extract page structure and metadata."""
    return await _backend_post(ctx, "/extract", {"url": url, "components": components})


@mcp.tool()
async def health_check(ctx: Context = None) -> dict[str, Any]:
    """Check service health."""
    return await _backend_get(ctx, "/health")


@mcp.tool()
async def providers_health(ctx: Context = None) -> dict[str, Any]:
    """Inspect provider health and cooldown state."""
    return await _backend_get(ctx, "/providers/health")


@mcp.tool()
async def service_metrics(ctx: Context = None) -> dict[str, Any]:
    """Get a canonical overview of service health: cache stats, provider status, and recent request summary. Mirrors GET /metrics."""
    return await _backend_get(ctx, "/metrics")


app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("EWS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("EWS_MCP_PORT", "8092"))
    uvicorn.run(app, host=host, port=port)