# Decision Summary

This is the short version of the master plan.

## Core decisions

### 1. Keep `enhanced-websearch` as the core backend

Do not replace it.

Reasons:

- best current service boundary
- already supports HTTP + MCP
- easier to fix than to replace
- other repos are better as references than substitutes

### 2. Keep clients thin

The backend should own the actual research workflow.

Client surfaces:

- Open WebUI wrapper
- MCP
- direct API

Wrappers should not contain their own orchestration logic.

### 3. Product shape

Use three practical capability tiers:

- `quick_search`
- `research`
- `deep_research`

Publicly:

- `/search` stays Perplexity-compatible quick search
- `/research` supports `auto`, `research`, `deep`

### 4. Separate raw tools from smart tools

Raw tools:

- `search`
- `fetch`
- `extract`

Smart tools:

- `research`
- `deep_research`

Reason:

- agents need both low-level retrieval and high-level compressed research

### 5. Retrieval and synthesis must be separate

Snippets are evidence, not the final answer.

Research output must be synthesized from evidence, with citations attached.

### 6. Use multiple model roles

Do not use one model for everything.

Recommended role split:

- routing / triage: small fast model
- planning: medium model
- verification: medium model
- final synthesis: stronger model

### 7. Vane is optional until proven stable in the backend

Current tested reality:

- direct Vane can work well with the right model setup
- current best-known working direct setup:
  - chat model: `opencode-go/mimo-v2-omni`
  - embedding model: `text-embedding-3-small`
- backend `/research` still does not surface Vane output correctly

Decision:

- do not make Vane the foundation of the product yet
- treat it as a validated optional layer
- keep direct provider retrieval as the grounding backbone

### 8. Add progress visibility, but do not build a job system yet

Use SSE for long-running research first.

Do not introduce durable job orchestration until it is actually needed.

### 9. Avoid unnecessary persistence early

Do not add SQLite for request-state workflow in the first pass.

Start with:

- request-scoped memory
- in-memory cache

## Core non-decisions (defer for now)

- durable job queue architecture
- saved report/product polish
- cross-request persistent research store
- heavy analytics/dashboard work
- broad provider expansion before the orchestration spine is stable

## Recommended build order

1. validation and contract freeze
2. quick search hardening
3. planning/routing foundation
4. research pipeline refactor
5. Vane integration repair
6. progress streaming
7. Open WebUI wrapper repair
8. provider expansion and hardening
9. evaluation suite
10. optional product polish

## The one-sentence strategy

**Make quick search solid, make research actually synthesized, keep Vane optional until the backend integration proves itself, then polish the client surfaces.**
