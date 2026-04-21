# MP-02 Planning and Routing Foundation Test Plan

This artifact is the concrete review and validation plan for MP-02.

It exists to validate that the new planner and routing foundation improves `POST /research` and `auto` mode selection without turning the system into an unbounded, planner-dependent workflow.

It is intentionally test-focused.
It does not authorize research-scope expansion beyond the bounded planner foundation defined in the master plan.

## Scope

This plan covers:

- MP-02 acceptance validation for bounded LLM-assisted planning and routing
- `POST /research` request routing behavior for `auto`, `research`, and `deep`
- planner contract behavior in `app/services/planner.py`
- orchestrator use of planner outputs in `app/services/orchestrator.py`
- decision-shape validation for routing decisions and research plans
- heuristic fallback behavior when planner output is missing, invalid, slow, or unavailable
- review checks that keep `/search` and non-goals intact while MP-02 is introduced

This plan does not cover:

- final research answer quality beyond planner/routing prerequisites
- deep synthesis quality work intended for MP-03
- Vane integration repair intended for MP-04
- streaming/progress work intended for MP-05
- wrapper repair intended for MP-06

## MP-02 acceptance anchor

MP-02 is only complete when all of the following are true:

- `auto` can choose quick vs research behavior correctly for representative queries
- planner output is structured and bounded
- heuristic fallback works when planner assistance fails
- planner decisions remain auditable in diagnostics/logging
- trivial requests do not over-escalate into research
- MP-02 does not violate the frozen `/search` non-goals

## Current implementation observations to validate

These observations come from the current code and should be verified, not assumed:

- `QueryPlanner.choose_mode()` is currently heuristic-only and returns `fast`, `research`, or `deep`
- `QueryPlanner.initial_plan()` currently returns a list of simple `{text, purpose}` items, not a formal bounded schema
- `ResearchOrchestrator.execute_search()` always calls `choose_mode()` and `initial_plan()` before retrieval work begins
- only `research` currently increases iteration count dynamically from request budget; non-research modes break after one cycle
- current planner behavior is not clearly separated into routing decision schema versus research plan schema
- current planner path does not expose an explicit fallback reason when routing/planning degrades to heuristics
- current code uses internal mode `fast`, while the master plan for MP-02 refers to quick-vs-research routing semantics
- `/search` remains the Perplexity-compatible quick path and must not become planner-dependent during MP-02

## Review targets

The MP-02 review should confirm or reject the following design targets before the milestone is marked complete.

### Target 1 - Bounded planner behavior

Planner output must be bounded in shape and execution consequences.

Validate that:

- routing output has a fixed schema with only allowed mode values
- research plan output has an explicit maximum number of steps/subqueries
- planner output cannot silently increase runtime beyond configured mode budgets
- planner output cannot create open-ended follow-up loops
- planner output is rejected or normalized if fields are missing, extra, or malformed

Blocking findings include:

- planner output can request arbitrary iteration counts
- planner output can inject free-form instructions that bypass orchestrator limits
- planner output shape is unvalidated or effectively unbounded

### Target 2 - Quick-vs-research routing correctness

`auto` should send trivial requests down the quick path and route research-worthy requests into bounded research behavior.

Validate that:

- simple factual lookups stay on the quick path
- comparison, recommendation, or clearly multi-source questions can route to research or deep as intended
- ambiguous cases are resolved consistently enough to be testable
- route choice is explainable from structured decision fields rather than prompt folklore alone

Blocking findings include:

- trivial lookups routinely route to research
- clearly research-grade prompts stay on quick path without explicit reason
- routing behavior cannot be inspected or explained after the fact

### Target 3 - Heuristic fallback

Planner assistance must be optional for system usefulness.

Validate that:

- planner timeout, malformed output, transport failure, or model unavailability all trigger a usable fallback
- fallback chooses a reasonable mode and bounded plan using heuristics
- fallback path is logged or surfaced in diagnostics clearly enough for review
- fallback does not break request success if a non-planner path is viable

Blocking findings include:

- request failure when planner fails and heuristics could have answered
- fallback produces materially different contract shape from planner-driven execution
- fallback reason is invisible in diagnostics/logging

### Target 4 - Structured decision schema

The planner foundation should distinguish routing from plan generation.

Validate that:

- routing decision schema is separate from research plan schema
- both schemas have explicit required and optional fields
- allowed mode names map cleanly to canonical public/internal semantics
- schema versions or normalization rules are documented well enough for future test fixtures

Suggested minimum routing-decision fields:

- `selected_mode`
- `reason_codes`
- `confidence`
- `fallback_used`

Suggested minimum research-plan fields:

- `steps`
- `max_iterations`
- `breadth`
- `needs_recency_check`
- `plan_source`

Blocking findings include:

- planner returns only free-form prose
- routing and planning are conflated into one untyped blob
- schema semantics drift from canonical mode mapping

### Target 5 - Non-goal enforcement

MP-02 must not quietly redefine `/search` or turn planner success into a prerequisite for normal operation.

Validate that:

- `/search` remains low-latency-first and mostly non-LLM
- `/search` does not require planner success to remain useful
- MP-02 work stays concentrated in `/research` and planner foundation code rather than spreading research behavior into `/search`
- internal naming changes do not break the user-visible quick-search contract

Blocking findings include:

- `/search` starts depending on planner calls for routine usefulness
- MP-02 adds heavy synthesis or iterative behavior to `/search`
- quick-search semantics become harder to distinguish from research semantics

## Test scenarios

Each scenario should record:

- query
- endpoint used
- request payload
- planner availability state
- selected mode
- planner decision payload or fallback indicator
- query-plan payload
- iteration count
- observed latency
- whether schema validation passed
- whether behavior matched expectation
- notes on diagnostics/logging

### Scenario 1 - Trivial factual query stays quick

Goal:

- verify `auto` does not over-escalate an easy request

Example queries:

- `capital of canada`
- `python current stable version`
- `who is the CEO of Microsoft`

Endpoint:

- `POST /research` with `mode=auto`

Pass checks:

- selected mode is the quick path equivalent, not `research` or `deep`
- routing decision uses allowed schema fields only
- query plan is bounded to the minimal plan shape
- total iteration count remains one unless an explicit bounded exception is documented
- diagnostics make the route choice understandable

Fail checks:

- trivial lookup routes to `research` or `deep` without clear, stable justification
- planner emits a multi-step plan for a one-hop fact lookup
- route choice cannot be explained from the recorded decision data

### Scenario 2 - Research-worthy comparison routes upward

Goal:

- verify `auto` can escalate a clearly multi-source question

Example queries:

- `compare Claude 4 and GPT-5 for coding tasks`
- `best note-taking apps for researchers in 2026`
- `should I use Docker Compose or Kubernetes for a small SaaS`

Endpoint:

- `POST /research` with `mode=auto`

Pass checks:

- selected mode is `research` or `deep` according to defined routing rules
- decision schema includes explainable reason codes such as comparison, recommendation, or complexity
- plan contains bounded expansion steps relevant to the question
- plan does not exceed configured maximum step count

Fail checks:

- obviously research-grade query remains on the quick path
- route escalates but plan is unbounded, vague, or free-form
- plan shape differs unpredictably across similar runs without explanation

### Scenario 3 - Explicit research bypasses route ambiguity cleanly

Goal:

- verify explicit user mode remains authoritative where intended

Example query:

- `summarize the latest browser engine changes affecting extension developers`

Endpoint:

- `POST /research` with `mode=research`

Pass checks:

- selected mode remains `research`
- routing decision schema records that the user-selected mode was honored
- planner still returns a bounded plan rather than improvising open-ended steps
- no quick-path downgrade occurs unless explicitly defined by contract

Fail checks:

- explicit `research` gets silently rewritten to quick path
- planner ignores requested mode and emits a conflicting decision
- bounded plan guarantees disappear when mode is explicit

### Scenario 4 - Explicit deep remains bounded

Goal:

- verify the strongest path still honors limits

Example query:

- `investigate the main arguments for and against banning non-compete clauses in US tech employment`

Endpoint:

- `POST /research` with `mode=deep`

Pass checks:

- selected mode remains `deep`
- plan breadth and iteration count are greater than `research` where defined, but still capped
- diagnostics show bounded values rather than open-ended instructions
- follow-up generation remains constrained by mode budget and orchestration limits

Fail checks:

- `deep` means effectively unlimited follow-ups
- plan omits hard ceilings on steps or iterations
- orchestrator behavior exceeds configured deep budget because of planner output

### Scenario 5 - Ambiguous query is stable enough to review

Goal:

- verify `auto` handles borderline cases consistently

Example queries:

- `best SSD for gaming`
- `latest React compiler status`
- `is Rust worth learning for backend work`

Endpoint:

- `POST /research` with `mode=auto`

Pass checks:

- route choice is consistent with documented heuristics or schema reasons
- if borderline queries split across quick and research, the reasons are inspectable and predictable enough to fixture
- planner confidence and fallback markers help explain close calls

Fail checks:

- similar borderline queries route randomly with no structured rationale
- no decision metadata exists to explain ambiguous outcomes

### Scenario 6 - Planner timeout fallback

Goal:

- verify timeout degrades gracefully

Setup ideas:

- point planner model config at a non-responsive endpoint in a controlled environment
- inject an artificial timeout in planner service tests

Example query:

- `compare major self-hosted password managers`

Pass checks:

- request still completes through heuristic fallback if retrieval path is otherwise available
- fallback marker is present in diagnostics/logging
- selected mode and plan remain bounded
- contract shape remains identical to normal execution

Fail checks:

- request fails solely because planner timed out
- fallback mode is missing or nonsensical
- timeout path produces a different output contract

### Scenario 7 - Planner malformed output fallback

Goal:

- verify schema validation protects the orchestrator

Setup ideas:

- unit-test planner normalization against invalid JSON
- simulate missing required fields, extra fields, wrong enum values, or over-limit step counts

Example malformed cases:

- missing `selected_mode`
- unsupported mode value
- step list longer than configured maximum
- prose blob instead of structured payload

Pass checks:

- malformed output is rejected or normalized deterministically
- heuristic fallback is used when repair is not possible
- diagnostics/logging preserve the reason for fallback
- orchestrator never executes an unvalidated plan blindly

Fail checks:

- invalid planner output reaches orchestration unchanged
- malformed output crashes the request path
- extra planner fields silently alter runtime without validation

### Scenario 8 - Non-goal enforcement on `/search`

Goal:

- verify MP-02 does not regress quick-search semantics

Example queries:

- `OpenAI pricing API`
- `capital of japan`

Endpoint:

- `POST /search`

Pass checks:

- response shape remains `PerplexitySearchResponse`
- request remains useful when planner is disabled or failing
- no research-style iterative behavior appears in the public `/search` contract
- latency and path shape remain aligned with MP-01 guardrails

Fail checks:

- `/search` now depends on planner success for routine usefulness
- `/search` exposes research-planning artifacts publicly
- `/search` becomes slower because it is using planner-driven orchestration by default

## Schema review checklist

Before implementation is accepted, review the concrete planner contracts and confirm:

- routing decision schema has documented required fields
- research plan schema has documented required fields
- schemas define hard bounds for list lengths and iteration-related fields
- enum values align with canonical mode mapping documentation
- schema validation or normalization location is obvious from code structure
- planner-produced diagnostics are safe to log and useful to debug
- future fixtures can be authored from the schema without reverse-engineering prompt text

## Suggested test levels

### Unit tests

Focus on:

- routing classification and normalization
- schema parsing/validation
- heuristic fallback selection
- max-step and max-iteration clamping
- malformed planner payload rejection

Likely files:

- `app/services/planner.py`
- planner schema or contract modules added during MP-02

### Integration tests

Focus on:

- `POST /research` with `mode=auto`, `research`, and `deep`
- planner success versus timeout/failure behavior
- diagnostics/query-plan visibility
- bounded orchestration behavior in `app/services/orchestrator.py`

Likely files:

- `app/main.py`
- `app/services/orchestrator.py`
- API route tests if present or added

### Review-only checks

Focus on:

- `/search` non-goal preservation
- canonical mode naming consistency
- planner abstraction staying narrow and auditable
- prevention of free-form, untyped planner control over orchestration

## Required evidence to mark MP-02 done

Record at minimum:

- representative `auto` routing outcomes for trivial, ambiguous, and research-grade queries
- one successful planner-driven bounded decision example
- one planner-timeout fallback example
- one malformed-output fallback example
- evidence that `/search` still works without planner dependence
- pass/fail judgment against each MP-02 acceptance anchor item
- any blocking review findings or residual risks

## Suggested residual-risk notes

Even if MP-02 passes, explicitly note whether any of the following remain true:

- routing heuristics and planner decisions still need calibration on benchmark sets
- schema exists but lacks stable versioning for future fixtures
- diagnostics are present but too weak for rapid production triage
- mode naming still risks confusion between public quick semantics and internal `fast`

## Exit rule

Do not mark MP-02 complete merely because a planner module exists.

Mark it complete only when:

- route selection is demonstrably correct enough for representative queries
- planner and plan outputs are bounded and structured
- heuristic fallback is proven under failure conditions
- `/search` non-goals remain intact
- review finds no blocking regression in routing, schema safety, or bounded execution
