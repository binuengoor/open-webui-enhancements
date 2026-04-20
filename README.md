# Enhanced Websearch Service

Standalone research backend for Open WebUI Perplexity-style tooling.

## What this is

This service is the canonical research engine. It owns:

- query normalization and planning
- mode semantics (auto, fast, deep, research)
- provider routing with cooldowns, fallback, and budgets
- search result normalization and fusion
- page fetch/extraction and optional PDF extraction
- optional Vane deep synthesis
- evidence/citation assembly
- structured response construction
- diagnostics, provider traces, and cache reporting
- MCP tools mounted on the same ASGI app at `/mcp`

The Open WebUI workspace tool becomes a thin HTTP wrapper.

## Project structure

- app/main.py: FastAPI app + dependency wiring
- app/api/routes.py: HTTP endpoints
- app/core/config.py: YAML + env config loading
- app/providers/: provider implementations + router
- app/services/: planner, ranker, fetch/extract, vane, orchestrator
- app/cache/memory_cache.py: in-memory TTL cache
- config/config.yaml: final service configuration used by compose
- config/config.sample.yaml: template you can copy from
- docker-compose.yml: single publishable service + optional dependencies

## API

### POST /search

Perplexity Search API-compatible endpoint for Open WebUI and other clients.

Request body supports at minimum:

{
  "query": "string or string[]",
  "max_results": 10
}

It returns a JSON object with `id`, `results`, and optional `server_time`.
Each result contains `title`, `url`, and `snippet`.

Optional Perplexity-style extensions are also accepted, including:

- display_server_time
- country
- max_tokens
- max_tokens_per_page
- search_language_filter
- search_domain_filter
- search_recency_filter
- search_after_date_filter
- search_before_date_filter
- last_updated_after_filter
- last_updated_before_filter
- search_mode
- mode

The endpoint also enforces:

- domain filters
- language filters (best-effort by URL/language metadata)
- recency/date filters when date metadata is available
- per-page and total token limits for snippets

Unknown fields are ignored.

### POST /internal/search

Request body:

{
  "query": "string",
  "mode": "auto|fast|deep|research",
  "source_mode": "web|academia|social|all",
  "depth": "quick|balanced|quality",
  "max_iterations": 4,
  "include_citations": true,
  "include_debug": false,
  "include_legacy": false,
  "strict_runtime": false,
  "user_context": {}
}

Response shape is stable and includes:

- query, mode, direct_answer, summary
- findings, citations, sources, follow_up_queries
- diagnostics: runtime, query_plan, provider_trace, cache, errors, warnings
- timings.total_ms
- confidence

Optional legacy output is opt-in with include_legacy.

### GET /health

Returns service liveness.

### GET /providers/health

Returns provider-level state:

- enabled
- cooldown_until
- consecutive_failures
- last_success_at
- last_failure_at
- last_failure_reason

### POST /fetch

Fetches and extracts a single page.

### POST /extract

Returns structured metadata for a single page.

### GET /config/effective

Returns effective non-secret config for debugging.

### GET /metrics

Returns basic service metrics (cache size and provider count) for lightweight observability.

### MCP tools at /mcp

The same FastAPI app also mounts a FastMCP server at `/mcp`.

Available tools:

- search
- perplexity_search
- fetch_page
- extract_page_structure
- health_check
- providers_health

Optional bearer token:

- set `EWS_BEARER_TOKEN` to require `Authorization: Bearer <token>` on the HTTP surfaces, including `/mcp`
- leave it blank for local/trusted setups

## Provider routing behavior

Router strategy:

- weighted rotating provider order
- checks provider cooldown state
- retries up to mode-specific budget
- marks provider cooldown after rate-limit or repeated failures
- falls back to next eligible provider
- records provider trace in diagnostics

Mode defaults (configurable):

- fast: low attempts, low page count, one pass
- deep: broader retrieval, optional Vane
- research: iterative cycles with bounded follow-up queries
- auto: heuristic selection based on query profile

## Caching

In-memory cache for v1:

- search cache keyed by normalized query/mode/options
- page fetch cache keyed by URL
- separate TTL for recency-sensitive queries
- hit/miss and cache stats exposed in diagnostics

## Configuration

1. Copy .env.example to .env
2. Edit config/config.yaml and env values
3. Keep secrets in env vars only

LiteLLM gateway setup defaults:

- use one shared key: LITELLM_API_KEY
- choose active LiteLLM providers with comma-separated names:
  LITELLM_ENABLED_PROVIDERS=brave-search,serper,exa,tavily

Optional LLM result compiler (Perplexity `/search` response refinement):

- set `EWS_COMPILER_ENABLED=true` to enable
- set `EWS_COMPILER_MODEL_ID` to the LiteLLM chat model id to use
- set `EWS_COMPILER_BASE_URL` to your LiteLLM base (`.../v1` recommended)
- set `EWS_COMPILER_API_KEY` for a compiler-specific auth key
- if `EWS_COMPILER_API_KEY` is unset, compiler falls back to `LITELLM_API_KEY`

When compiler output is accepted, each result may also include optional grounding metadata:

- `citation_ids`: candidate ids used to ground the item
- `evidence_spans`: short grounded excerpts
- `confidence`: normalized 0..1 confidence from compiler
- `grounding_notes`: optional short rationale

Vane defaults:

- VANE_ENABLED controls whether deep/research flows can call Vane
- VANE_DEFAULT_MODE defaults to balanced and can be set to speed, balanced, or quality
- deep/research requests can still escalate to quality when the query warrants it

Startup logs include:

- active and disabled providers
- routing policy and cooldown settings
- whether Vane is enabled, its URL, and its default optimization mode
- cache settings

YAML sections:

- service
- routing
- modes
- providers
- cache
- scraping
- vane
- logging

## Run

Local:

- pip install -r requirements.txt
- uvicorn app.main:app --host 0.0.0.0 --port 8091

Docker compose:

- cp .env.example .env
- docker compose up -d --build

## Open WebUI wrapper guidance

Use a single-file Open WebUI tool that:

- accepts tool arguments
- calls POST /internal/search on this service
- returns the service JSON unchanged
- optionally proxies /fetch and /extract

Keep wrapper logic minimal and stateless.

## Optional bearer token

If you want to protect the HTTP service and MCP server, set `EWS_BEARER_TOKEN` in `.env`.
The Open WebUI wrapper will forward the same token automatically when it is present.
