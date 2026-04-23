# Current Runtime Architecture

## Canonical product shape

- `POST /search`
  - Perplexity-style concise local search
  - provider rotation, filtering, normalization, caching, and concise formatting
- `POST /research`
  - transparent streaming relay to Vane `POST /api/search`
  - no local synthesis, compiler rewrite, or response remapping
- `GET /compat/searxng` and `GET /compat/searxng/search`
  - SearxNG-shaped compatibility adapter
  - web via local provider rotation, media via upstream passthrough
- `POST /fetch`
  - local fetch/extract for one URL
- `POST /extract`
  - local structure/metadata extraction for one URL
- MCP
  - mirrors the HTTP surfaces with matching external contracts

## Internal boundaries

- local search path owns provider routing, caching, fetch/extract, ranking, and metrics
- research path owns only Vane relay request translation and SSE passthrough
- compat path owns SearxNG request/response adaptation
- config should expose only active runtime knobs

## Design rule

Keep only code that directly supports the current runtime. Delete or collapse anything that exists only for the older local research-synthesis architecture.
