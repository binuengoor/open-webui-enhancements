# `/search` Non-Goals

This is the canonical non-goals note for `POST /search`.

It exists to prevent scope creep and to keep Perplexity-compatible quick search fast, predictable, and cheap.

## Core position

`/search` is the concise quick-search endpoint.

It must remain:

- low-latency first
- retrieval-first
- mostly non-LLM
- operationally boring
- shape-compatible for Perplexity-style clients

If a request genuinely needs long-form synthesis, iterative exploration, or a higher time budget, it should move to `POST /research`.

## `/search` must not become

`/search` must not become:

- a hidden long-running research endpoint
- a disguised multi-iteration reasoning loop
- a mandatory planner-driven orchestration path
- a place where Vane is required for acceptable output
- a catch-all endpoint for every advanced use case

## Explicit non-goals

### Non-goal: heavy synthesis by default

`/search` is not responsible for producing a full research report.

That means it should not:

- spend most of its runtime on answer generation
- require a large-model synthesis pass for normal usefulness
- try to compress a complex research workflow into a quick-search contract

### Non-goal: mandatory LLM planning for routine queries

Heuristics are acceptable and preferred for the normal `/search` path.

That means `/search` should not:

- require planner success before returning useful results
- block on an LLM just to decide a normal profile
- silently escalate simple queries into expensive orchestration

### Non-goal: deep source investigation

`/search` is not the endpoint for broad or contradiction-heavy investigation.

That means it should not:

- run many-step follow-up loops by default
- chase exhaustive source coverage
- behave like deep research behind a quick-search response shape

### Non-goal: Vane dependence

`/search` must remain useful even when Vane is disabled, unhealthy, or absent.

That means it should not:

- require Vane for acceptable latency or quality
- hide Vane latency inside a nominal quick-search request
- be redesigned around Vane-first semantics

### Non-goal: wrapper-specific semantics

The Open WebUI wrapper or MCP client should not invent a different meaning for `/search`.

That means wrappers should not:

- reinterpret `/search` as long-form research
- map `/search` onto internal `deep` mode by default
- add client-side orchestration that changes the backend contract

## What `/search` is allowed to do

To avoid over-correcting, `/search` may still:

- rank and normalize provider output
- apply lightweight heuristic planning
- use a small optional cleanup/compiler pass if it is proven beneficial
- enforce filters and concise snippet shaping
- degrade gracefully when providers fail

Rule:

- any optional LLM assist on `/search` must be easy to disable and must not redefine the endpoint into research

## Escalation rule

If a use case needs any of the following, it should move to `/research` instead:

- multi-source synthesis as the primary value
- deliberate higher latency budget
- explicit depth control
- iterative follow-up queries
- report-style output with stronger narrative structure

## Acceptance check

A proposed `/search` change is out of bounds if it causes any of these outcomes:

- normal requests routinely feel slow because of synthesis work
- the endpoint becomes unusable without LLM or Vane assistance
- callers cannot reliably distinguish quick search from research
- implementation complexity grows because `/search` is absorbing research responsibilities
