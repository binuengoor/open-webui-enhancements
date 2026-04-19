# Open WebUI Enhancements

Tool-first Open-WebUI enhancements for grounded web research.

## Repository Layout

- enhanced-websearch/
  - enhanced_websearch.py: Single deployable tool artifact with internal sectioning
  - README.md: Tool-specific architecture, valves, and output schema

## Current Architecture

The repository now supports a single Open-WebUI surface: tools.

- The pipe/function implementation was removed.
- `enhanced_websearch.py` is the only supported execution path.
- Core logic is driven by one canonical research pipeline (`_run_research`) with a thin Open-WebUI entrypoint (`elevated_search`).

## Current Module

### enhanced-websearch

Structured web retrieval and research with:

- SearXNG search + query expansion + RRF
- Concurrent scraping with FlareSolverr fallback
- Optional Vane deep synthesis with fast fallback behavior
- Execution modes: `auto`, `fast`, `deep`, `research`
- Structured JSON response with citations and diagnostics
- Runtime-aware compatibility layer with strict mode and degraded diagnostics

See module docs: `enhanced-websearch/README.md`

## Usage in Open-WebUI

1. Open Admin Panel in Open-WebUI.
2. Import `enhanced-websearch/enhanced_websearch.py`.
3. Configure admin valves (SearXNG required; Vane optional unless deep mode is used).
4. Optionally configure user valves for mode/status/citations.

## Development Notes

- Keep scripts standalone and import-ready for Open-WebUI.
- Prefer configurable valves over hardcoded endpoints.
- Keep Open-WebUI glue thin; business logic belongs in internal helpers.
