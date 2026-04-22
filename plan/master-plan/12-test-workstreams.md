# Test Workstreams

This file defines the testing tracks that should run independently of implementation.

## General rule

A workstream is not complete when code merely exists.
It is complete when the relevant test conditions pass.

## MP-00 - Contract and decision freeze

### Test tasks
- verify one canonical mode-mapping note exists
- verify `/search` non-goals are explicit
- verify wrapper diagnosis artifact exists
- verify known-good Vane config is recorded canonically

## MP-01 - Quick search hardening

### Test scenarios
- simple factual query
- provider failure simulation
- cooldown/fallback scenario
- recency-sensitive query
- filter-bearing query

### Validate
- response shape matches expectations
- fallback works when a provider fails
- latency stays inside quick-search budget
- `/search` does not silently become a heavy research path

## MP-02 - Planning and routing foundation

### Test scenarios
- easy query that should remain quick
- medium query that should route to research
- ambiguous query for `auto`
- planner failure fallback path

### Validate
- planner outputs are bounded and structured
- heuristic fallback works
- `auto` does not over-escalate trivial queries

## MP-03 - Research synthesis refactor

### Test scenarios
- comparison query
- broad explainer query
- contradiction-heavy query
- recent topic with sparse evidence

### Validate
- `summary` is a real summary
- `direct_answer` is synthesized
- findings are not just raw excerpt copies
- citations map back to evidence cleanly
- research and deep modes behave differently enough to justify both

## MP-04 - Vane integration repair

### Test scenarios
- direct Vane vs backend Vane output comparison
- Vane timeout path
- Vane error/fallback path
- validated known-good direct Vane config

### Validate
- backend can surface successful Vane output
- backend falls back cleanly if Vane fails
- backend Vane behavior no longer differs wildly from direct Vane for the same input

## MP-05 - Progress streaming

### Test scenarios
- long-running research request
- progress event order
- client disconnect handling
- no-progress quick path

### Validate
- events are meaningful and ordered
- long-running jobs no longer appear hung
- progress schema remains stable

## MP-06 - Open WebUI wrapper repair

### Test scenarios
- wrapper quick-search invocation
- wrapper research invocation
- wrapper progress handling
- wrapper error handling

### Validate
- wrapper mirrors backend semantics
- wrapper does not mutate core meaning of modes
- wrapper surfaces useful status during long runs

## MP-07 - Provider expansion and hardening

### Test scenarios
- provider rotation under normal load
- provider rotation under rate-limit conditions
- source diversity checks
- free-tier exhaustion simulation where possible
- mode-aware provider ordering checks
- config validation for known vs unknown provider preference names
- cooldown behavior by failure type

### Validate
- provider strategy improves resilience
- more providers do not degrade answer quality disproportionately
- preferred and avoided providers are ordered correctly per mode
- invalid provider preference declarations fail fast during config load
- cooldown policy matches rate-limit, auth, and transient failure expectations
- live degraded-path fallback is demonstrated with captured evidence before the milestone is closed

### Latest validation note
- targeted router/config tests passed in Docker/provisioned environments
- live smoke validation passed for the new provider ordering path
- degraded-path live fallback proof is still outstanding

## MP-08 - Quality gates and evaluation suite

### Test scenarios
- generic weak answer detection
- low-citation answer detection
- contradiction detection
- benchmark regression set

### Validate
- quality gate failures are actually triggered when appropriate
- benchmark results improve versus current baseline

## MP-09 - Optional product enhancements

### Test scenarios
- export a completed research response as Markdown and YAML and verify both remain readable and complete
- inspect recent-run history with and without optional local file backing enabled
- verify the enhanced `/metrics` endpoint during normal operation and after a handled provider failure
- verify MCP returns the same core metrics/health information rather than a divergent duplicate surface
- restart the service with no optional history/report writing configured and confirm core search/research behavior is unchanged

### Validate
- saved-report artifacts match completed response data and are useful to a human without a database lookup path
- recent-run history remains bounded, understandable, and non-essential to request execution
- `/metrics` becomes the canonical diagnostics surface without spawning overlapping endpoints
- MCP mirrors the same data model rather than inventing a second interpretation
- MP-09 changes do not regress latency, reliability, or the thin-client architecture

## Suggested benchmark buckets

- factual lookup
- comparison
- recent/news
- technical how-to
- broad research
- contradiction-heavy topic
- sparse-evidence topic

## Required acceptance tracking

For each milestone, record at minimum:

- latency observed
- success/failure
- source/citation count where relevant
- major regressions
- pass/fail against milestone acceptance criteria
