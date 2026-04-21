# Recommended Execution Order

This phase order merges Hermes’s stronger milestone structure with Luna’s implementation caution and live validation findings.

## Guiding rule

Do not try to fix everything at once.

The right order is:

- lock contracts and product semantics early
- validate unstable dependencies before building around them
- fix the search path before the deep path
- repair research synthesis before polishing client UX
- add streaming only after there are meaningful execution stages to report

## Phase 0 - validation and decision freeze

Goal:

- lock direction before writing implementation code

Tasks:

- freeze product tiers:
  - `quick_search`
  - `research`
  - `deep_research`
- freeze public API semantics:
  - `/search` is Perplexity-compatible quick search
  - `/research` supports `auto`, `research`, `deep`
- confirm model role split:
  - routing
  - planning
  - verification
  - synthesis
- complete Vane validation matrix
- document current Vane findings and known-good direct configuration
- document why the current Open WebUI wrapper is weaker than MCP

Why first:

- avoids building the wrong architecture around Vane or unstable mode semantics

Exit criteria:

- architecture direction accepted
- Vane role explicitly decided
- public semantics frozen

## Phase 1 - interface contract cleanup

Goal:

- make the public contract unambiguous before changing internals

Tasks:

- finalize `/search` request/response contract
- finalize `/research` modes and response contract
- define parity rules for HTTP, MCP, and Open WebUI wrapper
- define progress-event schema

Why here:

- prevents the backend and wrappers from drifting while internals are changing

Exit criteria:

- one coherent contract for all three client surfaces

## Phase 2 - quick search hardening

Goal:

- make `/search` reliably boring

Tasks:

- tighten provider rotation
- improve cooldown and failure handling
- keep search mostly non-LLM
- confirm Perplexity-compatible response behavior
- optionally add DuckDuckGo or other provider refinements later, but only after the core rotation logic is stable

Why before research refactor:

- quick search is the lowest-risk, highest-frequency path
- it should become stable before more complex work is layered on top

Exit criteria:

- predictable fallback
- stable shape
- cheap, fast behavior

## Phase 3 - planning/routing foundation

Goal:

- replace purely heuristic research routing with bounded LLM-assisted planning

Tasks:

- add routing decision schema
- add research plan schema
- create planner abstraction over LiteLLM
- keep heuristic fallback path if planner fails
- keep planner outputs auditable and bounded

Why now:

- research refactor needs a real planning spine
- this should be in place before deeper synthesis changes

Exit criteria:

- `auto` can choose quick vs research path
- planner output is structured and bounded

## Phase 4 - research pipeline refactor

Goal:

- stop returning stitched excerpts and return actual grounded answers

Tasks:

- separate evidence gathering from synthesis
- convert snippets into evidence inputs only
- add final synthesis stage for `direct_answer` and `summary`
- add citation mapping that supports claims cleanly
- distinguish `research` from `deep_research`

Why this is central:

- this is the main product defect today
- without this, better models or Vane won’t fix the bad returned shape

Exit criteria:

- research output reads like an answer
- findings are synthesized, not just excerpt copies

## Phase 5 - Vane integration repair and validation-based reintegration

Goal:

- make Vane useful only where it has been proven useful

Tasks:

- align backend behavior with the documented Vane API contract
- verify backend actually waits for and parses the Vane result correctly
- surface Vane result in diagnostics and/or payload when appropriate
- add timeout and fallback rules by mode
- treat the direct Mimo/OpenAI configuration as the current known-good baseline for testing

Why after research refactor:

- otherwise Vane gets blamed for a response-shape problem that belongs to the backend
- the backend must first know how to produce a good final answer from evidence

Exit criteria:

- Vane either improves output measurably or is cleanly bypassed
- backend no longer hides or drops successful Vane output

## Phase 6 - progress streaming

Goal:

- make long-running research visible without inventing a job system too early

Tasks:

- add SSE to `/research`
- emit stable phase events
- map events into Open WebUI status updates
- define MCP fallback behavior for progress

Why here:

- only add streaming once the research stages are stable enough to report meaningfully

Exit criteria:

- long-running research looks alive
- clients no longer feel hung

## Phase 7 - Open WebUI wrapper repair

Goal:

- make the wrapper thin, stable, and boring

Tasks:

- ensure wrapper contract matches backend contract
- keep orchestration in backend only
- add progress handling
- confirm parity with MCP and direct API

Why after backend stabilization:

- wrapper fixes should reflect stable backend behavior, not chase moving internals

Exit criteria:

- wrapper works reliably for quick search and research

## Phase 8 - provider expansion and hardening

Goal:

- improve source diversity and resilience once the orchestration spine is trustworthy

Tasks:

- add missing providers selectively
- improve provider specialization by mode/category
- strengthen fallback policies
- only consider persistent cache expansion later if justified

Why late:

- adding providers before the orchestration is trustworthy mostly increases noise

Exit criteria:

- better resilience and source coverage without destabilizing the core

## Phase 9 - quality gates and evaluation suite

Goal:

- stop the system from becoming a confident nonsense machine

Tasks:

- add explicit verification prompts and rejection criteria
- create benchmark queries across quick/research/deep
- add regression fixtures
- define what counts as weak/generic/failed output

Why after the main spine exists:

- evaluation is more useful once the architecture is stable enough to judge consistently

Exit criteria:

- measurable quality improvement over the current baseline

## Phase 10 - optional product enhancements

Goal:

- add polish only after the core behaves properly

Possible work:

- saved reports
- admin metrics
- durable report storage
- analytics
- optional persistence beyond in-memory cache

Why last:

- none of this matters if the core research path is still weak

## Final recommendation

If forced to compress all of this into one sentence:

**Validate and freeze the contract first, make quick search solid, build a real research spine, repair Vane integration only after that, then add streaming and wrapper polish.**
