# Master Backlog

This is the execution backlog derived from the master plan.

Status values:

- `todo`
- `in_progress`
- `blocked`
- `done`

A backlog item is only `done` when:

- implementation is complete
- relevant tests pass
- acceptance criteria are met
- review does not uncover a blocking issue

## MP-00 - Contract and decision freeze

**Status:** todo

**Purpose:**
Freeze the public and internal semantics before implementation begins.

**Includes:**
- public `/search` fast-endpoint semantics
- public `/research` research-endpoint semantics
- intended end-state `/research depth=balanced|quality` contract
- internal mode mapping
- `/search` non-goals
- wrapper diagnosis artifact
- canonical known-good direct Vane config

**Dependencies:** none

**Done criteria:**
- mode mapping is documented in one canonical place
- wrapper diagnosis exists
- Vane baseline config is documented
- `/search` non-goals are explicit

## MP-01 - Quick search hardening

**Status:** todo

**Purpose:**
Make `/search` reliable, low-latency, and Perplexity-compatible.

**Includes:**
- provider rotation hardening
- cooldown/fallback behavior
- shape compatibility
- non-LLM-first behavior

**Dependencies:** MP-00

**Done criteria:**
- predictable provider fallback
- response shape remains stable
- latency stays within quick-search budget

## MP-02 - Planning and routing foundation

**Status:** todo

**Purpose:**
Introduce bounded LLM-assisted routing and planning for research paths.

**Includes:**
- routing decision schema
- research plan schema
- planner abstraction
- heuristic fallback path

**Dependencies:** MP-00

**Done criteria:**
- `auto` can choose quick vs research path
- planner output is structured and bounded

## MP-03 - Research synthesis refactor

**Status:** todo

**Purpose:**
Replace snippet assembly with true grounded synthesis.

**Includes:**
- evidence gathering vs synthesis separation
- synthesized `direct_answer`
- synthesized `summary`
- improved findings structure
- citation mapping support

**Dependencies:** MP-00, MP-02

**Done criteria:**
- research answers read like answers, not pasted excerpts
- citations still map cleanly to evidence

## MP-04 - Vane integration repair

**Status:** todo

**Purpose:**
Repair and validate backend Vane integration.

**Includes:**
- ensure backend waits for and parses Vane correctly
- surface Vane output when appropriate
- timeout/fallback logic
- validated model/provider configuration

**Dependencies:** MP-03

**Done criteria:**
- backend can surface successful Vane output
- failure modes fall back cleanly
- direct and backend Vane behavior are no longer grossly mismatched

## MP-05 - Progress streaming

**Status:** todo

**Purpose:**
Expose progress for long-running research.

**Includes:**
- SSE support
- progress event schema
- orchestrator stage emissions

**Dependencies:** MP-03

**Done criteria:**
- clients can observe meaningful progress states
- long-running research no longer feels hung

## MP-06 - Open WebUI wrapper repair

**Status:** todo

**Purpose:**
Make the wrapper thin, stable, and aligned with backend semantics.

**Includes:**
- contract parity with backend
- progress handling
- no backend logic duplication

**Dependencies:** MP-00, MP-05

**Done criteria:**
- wrapper works for quick search and research
- wrapper semantics mirror backend behavior

## MP-07 - Provider expansion and hardening

**Status:** in_progress

**Purpose:**
Improve breadth and resilience of provider behavior.

**Includes:**
- provider specialization by mode
- optional provider additions
- stronger cooldown/fallback logic
- config validation that keeps provider preference ordering honest

**Dependencies:** MP-01

**Implementation progress:**
- mode-aware provider preference ordering is implemented in the router
- cooldown behavior now varies by failure type instead of treating all provider failures the same
- LiteLLM-backed provider onboarding is simplified via `litellm_provider` auto-path expansion
- config validation now rejects provider preferences that reference unknown provider names
- router/config unit coverage was added for preference ordering, cooldown policy, and validation failures
- Docker/provisioned-environment validation passed for the targeted router/config test coverage
- live smoke validation passed for the new provider ordering path
- degraded-path live fallback proof is still missing, so milestone acceptance is not yet fully demonstrated
- terminology/contract cleanup is documenting the intended end-state public model, but current implementation still exposes older `quick|balanced|quality` research depth terminology in code and some docs

**Done criteria:**
- source diversity improves without destabilizing the system
- free-tier exhaustion pressure is reduced
- provider preference config stays consistent with declared providers
- live validation confirms the new ordering and cooldown behavior improve resilience under real provider failures

## MP-08 - Quality gates and evaluation suite

**Status:** todo

**Purpose:**
Add structured evaluation and failure detection.

**Includes:**
- quality gate prompts and rules
- benchmark queries
- regression fixtures
- failure criteria enforcement

**Dependencies:** MP-01, MP-03, MP-04

**Done criteria:**
- weak/generic answers are detectable
- measurable quality improvement over current baseline exists

## MP-09 - Optional product enhancements

**Status:** todo

**Purpose:**
Add a small amount of operator and usability polish only after the core is stable.

**Includes:**
- saved-report export from completed research responses as Markdown and YAML artifacts without introducing a report database
- a minimal run-history log for recent requests, focused on debugging and manual inspection rather than analytics
- enhancements to the single `/metrics` endpoint so it can expose provider health, cache stats, and recent request counters in one place; mirror the same data through MCP rather than adding parallel status surfaces

**Explicit anti-scope:**
- no job queue or durable orchestration system
- no mandatory database or cross-request state store
- no heavy analytics pipeline, dashboard product, or multi-user admin surface
- no changes that make the wrapper or frontend own backend logic
- no optional persistence layer beyond file-based report artifacts and lightweight run-history state

**Dependencies:** MP-08

**Done criteria:**
- each enhancement is optional, local-first, and can be removed without affecting core search/research behavior
- saved outputs come directly from completed request artifacts rather than a new long-lived report system
- diagnostics remain lightweight and useful for debugging without becoming a full admin product
- any persistence added is file-based or equivalently simple and remains non-essential to normal request handling
- enhancements do not compromise backend simplicity, latency expectations, or reliability
