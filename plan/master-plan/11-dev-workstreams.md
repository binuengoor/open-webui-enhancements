# Dev Workstreams

This file translates backlog items into development-focused work packages suitable for delegation.

## MP-00 - Contract and decision freeze

### Dev tasks
- write one canonical mode-mapping note
- write `/search` non-goals note
- write wrapper diagnosis artifact
- record current known-good direct Vane config in one canonical place

### Likely files
- `plan/master-plan/*`
- backend contract docs if they exist

### Risks
- terminology drift between public and internal modes
- wrapper assumptions remaining undocumented

## MP-01 - Quick search hardening

### Dev tasks
- review provider router behavior
- tighten cooldown and fallback logic
- ensure search path stays mostly non-LLM
- confirm Perplexity-compatible response shaping
- reduce provider-specific edge-case failures

### Likely files
- `app/providers/router.py`
- `app/providers/*`
- `app/services/orchestrator.py`
- `app/models/contracts.py`
- `app/api/routes.py`

### Risks
- breaking compatibility while trying to improve quality
- accidentally making `/search` too expensive or slow

## MP-02 - Planning and routing foundation

### Dev tasks
- define routing decision schema
- define research plan schema
- implement planner service abstraction over LiteLLM
- add heuristic fallback path
- add config for role-based models

### Likely files
- `app/services/planner.py`
- `app/models/contracts.py`
- `app/core/config.py`
- possibly new planner-related modules

### Risks
- planner becoming overpowered and slowing the system
- unbounded plans creating runaway research loops

## MP-03 - Research synthesis refactor

### Dev tasks
- separate evidence gathering from final answer synthesis
- replace excerpt-copy findings with synthesized findings
- generate real `summary` and `direct_answer`
- improve citation-to-claim mapping
- define behavioral difference between `research` and `deep`

### Likely files
- `app/services/orchestrator.py`
- `app/services/ranking.py`
- `app/services/compiler.py`
- `app/models/contracts.py`

### Risks
- losing grounding while improving fluency
- creating attractive but weakly cited answers

## MP-04 - Vane integration repair

### Dev tasks
- verify backend request exactly matches expected Vane contract
- verify backend parsing of Vane response shape
- surface Vane output into payload/diagnostics correctly
- add timeout and fallback policy by mode
- add better Vane error reporting

### Likely files
- `app/services/vane.py`
- `app/services/orchestrator.py`
- `config/config.yaml`
- `.env.example`

### Risks
- overfitting integration to one Vane model/provider combo
- making the backend block too long on Vane

## MP-05 - Progress streaming

### Dev tasks
- define event schema
- add SSE endpoint behavior or streaming mode
- emit orchestrator stage transitions
- keep event payloads stable and minimal

### Likely files
- `app/api/routes.py`
- `app/services/orchestrator.py`
- possibly new streaming helper module

### Risks
- adding streaming before stage semantics are stable
- leaking too much internal detail in status events

## MP-06 - Open WebUI wrapper repair

### Dev tasks
- align wrapper requests with backend contract
- align wrapper return handling with backend response shape
- map backend progress events to UI status updates
- keep wrapper free of orchestration logic

### Likely files
- `enhanced-websearch/enhanced_websearch.py`
- wrapper docs

### Risks
- wrapper inventing its own semantics again
- status handling diverging from backend progress event schema

## MP-07 - Provider expansion and hardening

### Dev tasks
- improve provider specialization by mode
- optionally add/enable missing providers
- tune failure handling and rotation weights
- document provider behavior assumptions
- validate provider preference config against declared providers

### Current implementation status
- mode-aware provider preferences are wired into the router
- failure handling now distinguishes rate-limit, auth, and transient cooldown behavior
- LiteLLM-backed providers are easier to add through config-only `litellm_provider` entries
- provider preference names are now validated during config load

### Likely files
- `config/config.yaml`
- `config/config.sample.yaml`
- `app/core/config.py`
- `app/providers/*`
- `app/providers/router.py`
- `tests/test_provider_router.py`
- `tests/test_config_provider_preferences.py`

### Remaining risks
- more providers may still increase noise instead of quality in live runs
- free-tier exhaustion may shift to newly preferred providers rather than truly improving
- degraded-path live fallback behavior still lacks captured proof, so the milestone should not be treated as closed yet

### Latest validation note
- targeted router/config tests passed in Docker/provisioned environments
- live smoke validation passed for the new provider ordering path
- live degraded-path fallback evidence is still missing, leaving the final resilience claim only partially validated

## MP-08 - Quality gates and evaluation suite

### Dev tasks
- encode quality gate rules
- create benchmark query set
- add regression fixtures
- define and implement failure thresholds

### Likely files
- `app/services/compiler.py`
- test fixtures/docs
- `plan/master-plan/*`

### Risks
- vague quality rules that cannot be enforced
- overfitting to too small a benchmark set

## MP-09 - Optional product enhancements

### Dev tasks
- define a simple saved-report export path for completed research results using existing response artifacts, preferring Markdown and YAML outputs for human readability
- add a minimal recent-run history log backed by local files or ephemeral state rather than a service database
- enhance the existing `/metrics` endpoint so it can expose provider health, cache stats, and recent request counters in one place
- mirror the same metrics/health data through MCP rather than adding a separate overlapping status surface
- document explicit anti-scope so polish work does not sprawl into a platform project

### Likely files
- `app/api/routes.py`
- `app/services/orchestrator.py`
- `app/models/contracts.py`
- `app/mcp_server.py`
- small docs under `plan/master-plan/*`
- local artifact/history helpers if needed

### Risks
- polishing the wrong layer before the backend is trustworthy
- accidentally introducing durable state assumptions into a local-first backend
- letting `/metrics` sprawl into a dashboard/analytics subsystem
- duplicating health/metrics surfaces instead of enhancing one canonical API + MCP path
