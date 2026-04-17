# Enhanced Websearch v1.1

This folder contains two Open-WebUI-ready variants:

Two variants live here:

- [enhanced_websearch.py](enhanced_websearch.py): tool version for direct retrieval
- [enhanced_websearch_pipe.py](enhanced_websearch_pipe.py): function/pipe version for model-driven research

## Which One To Use

- Use the tool version when you want fast search, fetch, extract, and citations with minimal orchestration.
- Use the pipe version when you want the model to drive iterative research, follow-up queries, and final synthesis.
- If you want Perplexity-like behavior, the pipe version is the better fit.

## Shared Capabilities

- SearXNG retrieval
- Query expansion
- Reciprocal Rank Fusion (RRF)
- Concurrent page scraping
- FlareSolverr fallback for blocked pages
- Optional Vane deep synthesis
- Temporal query enrichment
- Structured page extraction

## Files

## Open-WebUI Configurable Parameters

Open-WebUI exposes three configuration surfaces for these scripts.

### 1) Admin Tool Valves (global defaults)

Configured from Open-WebUI Admin Panel -> Tools -> this tool -> gear icon.

This module now exposes a curated valve surface: maximum 10 valves per script.

Tool script valves (10):

- SEARXNG_BASE_URL
- VANE_URL
- FLARESOLVERR_URL
- SEARCH_RESULTS_PER_QUERY
- PAGES_TO_SCRAPE
- ENABLE_VANE_DEEP
- VANE_CHAT_MODEL_PROVIDER_ID
- VANE_CHAT_MODEL_KEY
- VANE_EMBEDDING_MODEL_PROVIDER_ID
- VANE_EMBEDDING_MODEL_KEY

Pipe script valves (10):

- SEARXNG_BASE_URL
- VANE_URL
- FLARESOLVERR_URL
- SEARCH_RESULTS_PER_QUERY
- PAGES_TO_SCRAPE
- VANE_CHAT_MODEL_PROVIDER_ID
- VANE_CHAT_MODEL_KEY
- VANE_EMBEDDING_MODEL_PROVIDER_ID
- VANE_EMBEDDING_MODEL_KEY
- RESEARCH_MODEL

Everything else uses internal defaults tuned for typical self-hosted setups.

Important key-format note:

- Vane model keys must match `/api/config` exactly.
- Example: use `Xenova/nomic-embed-text-v1` (not `nomic-embed-text-v1`) when using the Transformers embedding provider.

### 2) User Valves (per-user behavior)

Configured by the user in chat/tool settings (when exposed by Open-WebUI).

- mode
- show_status_updates
- include_citations
- show_reasoning (default: false)
- max_iterations

### 3) Runtime Function Arguments (model/tool call level)

The model can set these while calling functions.

For elevated_search:
- query (required)
- mode (default: auto) values: auto, fast, deep, research
- source_mode (default: web) values: web, academia, social, all
- depth (default: balanced) values: quick, speed, balanced, quality

Query prefix overrides (both tool and pipe):

- Prefix with fast: to force fast mode for that request.
- Prefix with deep: to force deep mode for that request.
- If no prefix is provided, mode remains auto by default (or user valve override if configured).

Examples:

- fast: summarize Kubernetes ingress controller options
- deep: compare LiteLLM vs OpenRouter routing strategies

For the tool script, these are exposed as tool-call arguments.

For the pipe script, Open-WebUI uses the same behavior through the chat flow and the active model context.

For fetch_page:
- url (required)

For extract_page_structure:
- url (required)
- components (default: all) values: all or comma-separated list from headings, links, tables, sections, code_blocks, lists, meta

## Suggested Starter Defaults

If you are using Vane and SearXNG as upstream services:

- mode: auto
- source_mode: web
- depth: balanced
- max_iterations: 5
- SEARCH_RESULTS_PER_QUERY: 8
- PAGES_TO_SCRAPE: 5
- CONCURRENT_SCRAPE_WORKERS: 4
- ENABLE_VANE_DEEP: true
- RESEARCH_MODEL: your Open-WebUI configured model key

## Mandatory vs Optional Valves

Mandatory for both tool and pipe:

- SEARXNG_BASE_URL must point to a reachable SearXNG instance.

Mandatory if deep mode is enabled (selected or auto-escalated):

- VANE_URL must be reachable.
- VANE_CHAT_MODEL_PROVIDER_ID must be set.
- VANE_EMBEDDING_MODEL_PROVIDER_ID must be set.

Tool-specific research backend requirements:
None. Tool research planning is heuristic by default and does not require a separate model backend valve.

Mandatory for pipe version only if you want forced planning model override:

- RESEARCH_MODEL is optional. If empty, pipe planning uses the active Open-WebUI chat model by default.

## Notes

- This script does not write to the Open-WebUI database.
- Both scripts are import-ready as standalone Open-WebUI extensions.
- Deep mode requires Vane model provider IDs to be configured.
- The tool script uses heuristic research planning by default.
- Advanced tuning knobs (timeouts, RRF, language, time range, token limits) are intentionally internal defaults in v1.1 to keep the valve surface small.
- The pipe script uses Open-WebUI model calls for research planning and synthesis.
- The pipe returns concise markdown output (deep summary + top sources) instead of raw JSON.
- In deep mode, fast evidence and Vane output are fused into consensus points plus fast-only and Vane-only additions.
- Enable `show_reasoning` only when debugging, to keep responses clean.
- The tool version is best for attaching to a model as a callable search tool.
- The pipe version is best for model-driven research workflows and synthesis.
