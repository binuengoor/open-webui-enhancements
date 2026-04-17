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

- SEARXNG_BASE_URL
- VANE_URL
- FLARESOLVERR_URL
- REQUEST_TIMEOUT
- FLARESOLVERR_TIMEOUT
- USER_AGENT
- SEARCH_RESULTS_PER_QUERY
- QUERY_VARIANTS_LIMIT
- PAGES_TO_SCRAPE
- CONCURRENT_SCRAPE_WORKERS
- RRF_K
- SEARCH_CATEGORIES
- SEARCH_ENGINES
- SEARCH_LANGUAGE
- SEARCH_TIME_RANGE
- MAX_PAGE_CONTENT_CHARS
- MIN_CONTENT_CHARS
- INJECT_DATETIME
- DATETIME_FORMAT
- TIMEZONE
- ENABLE_VANE_DEEP
- VANE_CHAT_MODEL_PROVIDER_ID
- VANE_CHAT_MODEL_KEY
- VANE_EMBEDDING_MODEL_PROVIDER_ID
- VANE_EMBEDDING_MODEL_KEY
- VANE_TIMEOUT
- RESEARCH_MIN_ITERATIONS
- RESEARCH_MAX_CONTEXT_SOURCES
- IGNORED_DOMAINS

Tool-only admin valves:

- RESEARCH_BACKEND (default: heuristic)
- OLLAMA_URL
- OLLAMA_MODEL
- OLLAMA_TIMEOUT

Pipe-only admin valves:

- RESEARCH_MODEL
- RESEARCH_MODEL_TEMPERATURE
- RESEARCH_MODEL_MAX_TOKENS

### 2) User Valves (per-user behavior)

Configured by the user in chat/tool settings (when exposed by Open-WebUI).

- mode
- show_status_updates
- include_citations
- show_reasoning
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
- QUERY_VARIANTS_LIMIT: 4
- PAGES_TO_SCRAPE: 5
- CONCURRENT_SCRAPE_WORKERS: 4
- RRF_K: 60
- INJECT_DATETIME: true
- ENABLE_VANE_DEEP: true
- RESEARCH_MODEL: your Open-WebUI configured model key

## Mandatory vs Optional Valves

Mandatory for both tool and pipe:

- SEARXNG_BASE_URL must point to a reachable SearXNG instance.

Mandatory if deep mode is enabled (selected or auto-escalated):

- VANE_URL must be reachable.
- VANE_CHAT_MODEL_PROVIDER_ID must be set.
- VANE_EMBEDDING_MODEL_PROVIDER_ID must be set.

Mandatory for tool version only when RESEARCH_BACKEND=ollama:

- OLLAMA_URL must be reachable.
- OLLAMA_MODEL must exist in that Ollama instance.

Mandatory for pipe version only if you want forced planning model override:

- RESEARCH_MODEL is optional. If empty, the pipe falls back to the active Open-WebUI model from the current chat request.

## Notes

- This script does not write to the Open-WebUI database.
- Both scripts are import-ready as standalone Open-WebUI extensions.
- Deep mode requires Vane model provider IDs to be configured.
- The tool script uses heuristic research planning by default. Set RESEARCH_BACKEND=ollama to enable Ollama-assisted planning.
- The pipe script uses Open-WebUI model calls for research planning and synthesis.
- The tool version is best for attaching to a model as a callable search tool.
- The pipe version is best for model-driven research workflows and synthesis.
