# Master Architecture

## Executive summary

`enhanced-websearch` should remain the system of record for search and research behavior.

It should evolve into a **local-first, agent-friendly Perplexity-style backend** with three client surfaces:

1. Open WebUI wrapper
2. MCP server
3. direct API endpoints

The backend should own the real search and research loop. Clients should stay thin.

## Why keep this repo as the core

Compared with the other reviewed repos, `enhanced-websearch` is still the best fit because it already has:

- a dedicated API/service boundary
- provider routing and fallback
- MCP support
- page fetch/extract and citation assembly
- a shape that can serve multiple clients cleanly

External repos remain useful references only:

- `deep-search-portal` for deeper research ideas
- `open-webui-deep-research-tool` for Open WebUI research-loop ideas
- `OWUI-Toolset` for Open WebUI integration patterns

## Product definition

The product should explicitly expose two broad classes of capability:

### A. Raw retrieval tools

Purpose:

- give agents fast, deterministic access to search and fetch primitives
- keep token use low
- keep frontend agents in control when the problem is small

Examples:

- `search`
- `fetch`
- `extract`

### B. Smart research tools

Purpose:

- let the backend own longer reasoning/research loops
- compress many retrieved pages into fewer grounded findings
- reduce frontend context waste
- improve reliability over long tool chains

Examples:

- `research`
- `deep_research`

## Public mode structure

Recommended practical modes:

- `quick_search`
- `research`
- `deep_research`

Mapped to HTTP:

- `/search` remains the Perplexity-compatible quick-search path
- `/research` becomes the structured long-form path

Suggested public mode semantics on `/research`:

- `auto`
- `research`
- `deep`

Meaning:

- `auto` lets the planner choose the work level
- `research` returns a grounded report-style answer
- `deep` uses the strongest bounded iterative workflow

## Core architectural rule

Separate retrieval from synthesis.

The backend should not treat excerpts as final answers.

Pipeline should look like this:

1. normalize request
2. choose mode / route
3. generate plan
4. retrieve evidence
5. fetch / extract pages
6. rank / dedupe evidence
7. synthesize answer
8. run quality gate
9. format response for the client surface

## Tier behavior

### Quick search

Purpose:

- very low-latency factual lookup and navigation

Behavior:

- direct call to rotating provider pool
- bounded retry/fallback
- no mandatory LLM stage
- preserve Perplexity-compatible result format

Design rule:

- `/search` should stay cheap, predictable, and mostly retrieval-only

### Research

Purpose:

- grounded long-form answer with citations

Behavior:

- LLM planner creates bounded research plan
- backend retrieves evidence
- optional Vane draft/synthesis branch if healthy
- direct provider verification and enrichment
- final synthesis from grounded evidence
- quality gate before response

### Deep research

Purpose:

- more exhaustive, slower, higher-confidence exploration

Behavior:

- stronger planning budget
- more iterations
- contradiction/gap checks
- broader retrieval coverage
- stronger synthesis/report formatting

## Agent-first design implication

The backend is primarily serving agents, not humans directly.

That means it should optimize for:

- signal density
- token efficiency
- reliability
- compression of evidence into grounded findings

This strengthens the case for backend-owned research loops instead of expecting frontend agents to manually orchestrate long search/fetch chains.

## Model strategy

Do not use one model for everything.

Recommended role split:

- routing / triage: small fast model
- planning: medium model
- verification: medium model
- final synthesis: stronger model

Design rule:

- quick search remains mostly non-LLM
- expensive models are reserved for the stages where synthesis and judgment matter

## Vane strategy

### Architectural stance

Vane is useful, but it is not trustworthy enough to be the unquestioned center of the pipeline.

Current reality from testing:

- direct Vane results depend heavily on model/provider choice
- Gemini setup proved unreliable due to invalid model selection and later upstream 429s
- direct Vane worked well with:
  - chat model: `opencode-go/mimo-v2-omni`
  - embedding model: `text-embedding-3-small`
- even with that working direct setup, backend `/research` still does not surface Vane output correctly

### Conclusion

Treat Vane as:

- optional
- validated by direct testing
- always behind timeouts and fallback logic

Never let Vane replace grounded provider evidence.

## Progress visibility

Long research operations should expose live progress.

Recommended mechanism:

- SSE for HTTP/direct API
- Open WebUI wrapper maps progress events into status updates
- MCP can remain synchronous initially, with lighter status signaling if needed

Recommended status phases:

- `received`
- `routing`
- `planning`
- `retrieval`
- `vane`
- `fact_check`
- `followup`
- `synthesis`
- `quality_gate`
- `complete`

## Persistence stance

Do not introduce SQLite or durable job orchestration for request-state in the first pass.

Use:

- in-memory request-scoped state
- in-memory cache

Only add persistence later if it becomes necessary for:

- resumable jobs
- durable research history
- cross-request reuse
- auditability

## Current hard truth

The architecture should assume the current backend Vane integration is incomplete.

Reason:

- direct Vane can produce strong long-form results
- backend `/research` still returns snippet-style output
- backend `legacy.deep_synthesis` remains empty in observed tests

So the system should be designed around a retrieval-first reliable core, with Vane as a validated optional layer until the integration is repaired.

## Final merged recommendation

Use Hermes’s high-level agent/product framing, but keep Luna’s operational caution:

- backend remains canonical
- wrapper stays thin
- Vane remains optional until validated
- direct retrieval remains the grounding backbone
- research output must be synthesized, not excerpt-assembled
