# Enhanced Websearch (Tool-Only)

This module now provides a single Open-WebUI surface:

- [enhanced_websearch.py](enhanced_websearch.py): single deployable Open-WebUI tool artifact

The previous pipe/function implementation has been removed as part of the tool-first refactor.

## Architecture

`enhanced_websearch.py` contains one canonical internal execution path (`_run_research`) and thin public entrypoints:

- `elevated_search`: primary structured research tool
- `fetch_page`: direct fetch helper
- `extract_page_structure`: structural extraction helper

Capabilities:

- SearXNG retrieval
- Query expansion + Reciprocal Rank Fusion (RRF)
- Concurrent scraping
- FlareSolverr fallback for blocked pages
- Optional Vane deep synthesis with fast fallback behavior
- Iterative research mode with explicit stop conditions

## Runtime Support Matrix

Standard Python or server runtimes:

- Fully supported path
- Transport backend: `requests`
- HTML parser backend: `beautifulsoup4`
- Scrape execution: threadpool
- PDF extraction: `pypdf` or `PyPDF2` when available

Strict constrained runtimes (Pyodide-style browser constraints):

- Degraded but supported path
- Transport fallback: stdlib `urllib`
- HTML parsing fallback: basic text extraction (no full DOM fidelity)
- Scrape execution fallback: sequential (no threadpool)
- PDF extraction may be unavailable

Unsupported capability handling:

- The tool returns partial results when possible.
- Runtime limitations are reported in `diagnostics.runtime` and `diagnostics.warnings`.

## Open-WebUI Configurable Parameters

### 1) Admin Valves (global defaults)

Configured from Open-WebUI Admin Panel -> Tools -> this tool -> gear icon.

- `SEARXNG_BASE_URL`
- `VANE_URL`
- `FLARESOLVERR_URL`
- `SEARCH_RESULTS_PER_QUERY`
- `PAGES_TO_SCRAPE`
- `ENABLE_VANE_DEEP`
- `VANE_CHAT_MODEL_PROVIDER_ID`
- `VANE_CHAT_MODEL_KEY`
- `VANE_EMBEDDING_MODEL_PROVIDER_ID`
- `VANE_EMBEDDING_MODEL_KEY`
- `STRICT_COMPAT_MODE`

Environment variable defaults:

- `SEARXNG_URL` or `SEARXNG_BASE_URL`
- `VANE_URL`
- `FLARESOLVERR_URL`
- `SEARCH_RESULTS_PER_QUERY`
- `PAGES_TO_SCRAPE`
- `ENABLE_VANE_DEEP`
- `VANE_CHAT_MODEL_PROVIDER_ID`
- `VANE_CHAT_MODEL_KEY`
- `VANE_EMBEDDING_MODEL_PROVIDER_ID`
- `VANE_EMBEDDING_MODEL_KEY`
- `STRICT_COMPAT_MODE`

Advanced env overrides (internal defaults):

- `REQUEST_TIMEOUT`, `FLARESOLVERR_TIMEOUT`, `VANE_TIMEOUT`
- `CONCURRENT_SCRAPE_WORKERS`, `QUERY_VARIANTS_LIMIT`, `RRF_K`
- `SEARCH_CATEGORIES`, `SEARCH_ENGINES`, `SEARCH_LANGUAGE`, `SEARCH_TIME_RANGE`
- `MAX_PAGE_CONTENT_CHARS`, `MIN_CONTENT_CHARS`
- `INJECT_DATETIME`, `DATETIME_FORMAT`, `TIMEZONE`
- `RESEARCH_MIN_ITERATIONS`, `RESEARCH_MAX_CONTEXT_SOURCES`

### 2) User Valves (per-user behavior)

- `mode`
- `show_status_updates`
- `include_citations`
- `show_reasoning`
- `max_iterations`

### 3) Runtime Arguments (tool call)

For `elevated_search`:

- `query` (required)
- `mode` (default: `auto`) values: `auto`, `fast`, `deep`, `research`
- `source_mode` (default: `web`) values: `web`, `academia`, `social`, `all`
- `depth` (default: `balanced`) values: `quick`, `speed`, `balanced`, `quality`

Optional mode prefixes in query:

- `fast: ...`
- `deep: ...`

For `fetch_page`:

- `url` (required)

For `extract_page_structure`:

- `url` (required)
- `components` (default: `all`)

## Response Contract

`elevated_search` now returns a structured JSON object with stable top-level fields:

- `query`
- `mode`
- `direct_answer`
- `summary`
- `findings`
- `citations`
- `sources`
- `follow_up_queries`
- `diagnostics`
- `timings`
- `confidence`

Transition compatibility:

- Legacy payload fields are still available under `legacy` for migration.
- New integrations should read top-level structured fields first.

Runtime diagnostics:

- `diagnostics.runtime` reports active runtime backends and degraded reasons.
- `diagnostics.warnings` includes runtime degradation warnings when applicable.

## Notes

- This script does not write to the Open-WebUI database.
- Deep mode requires Vane provider IDs to be configured.
- If deep synthesis fails or is low confidence, the tool falls back to fast evidence.
