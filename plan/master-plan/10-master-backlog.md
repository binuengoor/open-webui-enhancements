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
- public `/search` and `/research` semantics
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

**Status:** todo

**Purpose:**
Improve breadth and resilience of provider behavior.

**Includes:**
- provider specialization by mode
- optional provider additions
- stronger cooldown/fallback logic

**Dependencies:** MP-01

**Done criteria:**
- source diversity improves without destabilizing the system
- free-tier exhaustion pressure is reduced

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
Add polish only after the core is stable.

**Includes:**
- saved reports
- lightweight metrics/admin views
- optional persistence improvements

**Dependencies:** MP-08

**Done criteria:**
- enhancements do not compromise backend simplicity or reliability
