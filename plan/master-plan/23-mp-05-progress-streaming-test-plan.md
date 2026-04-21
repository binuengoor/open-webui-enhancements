# MP-05 Progress Streaming Test Plan

This artifact defines how to validate the MP-05 goal of exposing meaningful progress for long-running research without changing the core product contract.

It is intentionally test-focused.
It does not authorize job queues, background task persistence, or wrapper-specific behavior beyond proving that backend progress signals are stable and usable.

## Scope

This plan covers:

- MP-05 acceptance validation for backend progress streaming on long-running research paths
- SSE endpoint behavior and event-stream correctness
- stable event ordering and meaningful stage transitions from the orchestrator
- behavior when clients disconnect before the request completes
- regression protection for non-streaming response paths
- regression protection for quick search and other no-progress paths
- success criteria for deciding whether "progress streaming works"

This plan does not cover:

- MP-03 grounded synthesis quality beyond what is needed to exercise long-running research stages
- MP-04 Vane integration repair except where Vane participation changes stage emission behavior
- MP-06 wrapper-side rendering of backend progress events
- a full async job system, resumable runs, or persisted progress history
- benchmark automation or scoring work intended for MP-08

## Baseline references

Use these repo artifacts as the source of truth while running this plan:

- `plan/master-plan/02-recommended-execution-order.md`
- `plan/master-plan/03-decision-summary.md`
- `plan/master-plan/10-master-backlog.md`
- `plan/master-plan/11-dev-workstreams.md`
- `plan/master-plan/12-test-workstreams.md`
- `plan/master-plan/13-status-tracker.md`

Current code paths relevant to this test plan:

- `app/api/routes.py`
- `app/services/orchestrator.py`
- `app/models/contracts.py`
- `app/services/vane.py`
- `README.md`

## Current implementation observations to validate

These observations come from the current repository state and should be re-checked once MP-05 implementation exists:

- `POST /research` currently returns one JSON response from `app/api/routes.py` and has no SSE behavior yet
- `ResearchOrchestrator.execute_search(...)` already has meaningful internal phases that can become stream stages, including routing, planning, provider search cycles, fusion/ranking, page fetch, evidence gathering, optional Vane, optional vet/fallback, and final response assembly
- `POST /search` is the quick path and should remain a normal non-streaming request/response flow
- the current response contracts in `app/models/contracts.py` do not yet define progress-event payloads
- `app/services/vane.py` currently calls Vane with `stream: false`, so MP-05 should treat backend progress as orchestrator progress rather than raw Vane token streaming
- there is no `tests/` directory in the current repo state, so this plan assumes initial validation may combine new automated tests with manual `curl` or client-side inspection

## MP-05 acceptance anchor

MP-05 is only complete when all of the following are true:

- long-running research requests expose progress through a stable SSE-compatible stream
- emitted events are ordered, parseable, and tied to real backend stages rather than synthetic timers
- stage transitions are meaningful enough that operators can tell whether the request is planning, searching, fetching, synthesizing, falling back, or completing
- client disconnects do not leave obviously broken behavior such as noisy exceptions, wedged tasks, or indefinite work with no cancellation path
- existing non-streaming behavior still works for clients that do not ask for progress
- quick-search behavior remains normal and is not accidentally promoted into streaming research semantics

## Proposed validation model

Use a layered test strategy:

1. contract inspection for the streaming endpoint shape and event schema
2. deterministic automated checks for event ordering and event payload fields
3. manual streaming validation with a real HTTP client that can observe SSE framing
4. regression checks for non-streaming `/research` and quick `/search` behavior
5. resilience checks for disconnects, early closes, and degraded backends

Prefer deterministic test doubles for timing-sensitive ordering checks. Use live upstream provider calls only for final acceptance and operator-level confirmation.

## Required event contract to validate

The implementation may choose exact field names, but the test plan assumes MP-05 will define a minimal stable event schema.

At minimum, validate that each streamed event includes enough information to answer:

- which request emitted the event
- what stage the backend is in
- whether the event is informational, warning, error, heartbeat, or terminal
- whether the request is still running or has completed
- optional progress detail that is stable and user-meaningful without leaking unnecessary internals

The exact schema can vary, but the test suite should reject these classes of problems:

- event payloads that differ arbitrarily across stages
- missing stage identifiers
- terminal events with no clear success or failure meaning
- stage messages that are purely decorative and not tied to actual execution state
- event streams that require parsing logs rather than the stream payload itself to understand status

## Suggested stage vocabulary to validate

MP-05 should not be considered successful unless the emitted stages are few, stable, and reflect real control-flow boundaries.

The test plan should validate that the final implementation uses a stage set close to the existing orchestrator lifecycle, such as:

- `started`
- `routing`
- `planning`
- `searching`
- `fetching`
- `evidence`
- `vane`
- `synthesis`
- `vetting` or `fallback`
- `completed`
- `error`

The exact labels may differ, but tests should ensure:

- stage names are finite and documented
- repeated events within one stage remain coherent
- stage changes correspond to real execution transitions
- optional branches like Vane and fallback are only emitted when actually used

## Test environment and instrumentation

For reliable validation, capture at minimum:

- request payload and headers used to request streaming versus non-streaming behavior
- response headers, especially `Content-Type`, cache-related headers, and connection behavior
- raw event stream bytes for at least one successful long-running run
- server logs correlated by request id if request ids are emitted
- elapsed time between first byte, intermediate events, and terminal completion
- whether upstream provider behavior was real, mocked, slowed, or forced to fail

Useful tooling includes:

- `curl -N` or equivalent SSE-capable CLI client
- a small local mock client that records event order and timestamps
- mocked or monkeypatched provider/search/fetch/Vane steps to force deterministic delays
- application logs with request ids enabled

## Detailed scenarios

### Scenario 1 - SSE endpoint returns a valid event stream

Purpose:

Prove that the streaming research path uses correct SSE behavior rather than buffered JSON.

Setup:

- enable whatever request knob selects streaming for long-running research
- use a query that reliably exercises multiple orchestration phases
- use deterministic delays in one or more backend phases if needed so intermediate events are observable

Steps:

- send a streaming research request
- capture status code, headers, and raw response body
- verify the client receives incrementally framed events before final completion

Validate:

- HTTP status is successful for the happy path
- `Content-Type` is SSE-compatible such as `text/event-stream`
- the response is not buffered until the end
- event framing is valid for an SSE client
- the stream ends cleanly with a terminal event or documented close behavior

Fail examples:

- endpoint still returns ordinary JSON while claiming to stream
- first event is delayed until the whole search finishes
- framing is malformed or event boundaries are ambiguous
- stream ends without any terminal success or failure signal

### Scenario 2 - Event ordering matches real execution order

Purpose:

Prove that progress events are ordered and tied to actual backend phases.

Setup:

- use mocked or slowed dependencies so phases are observable
- prefer deterministic staging over live internet timing

Steps:

- run a streaming research request
- record all event names, timestamps, and stage payloads
- compare emitted order to the orchestrator control flow

Validate:

- the first event is a start or routing event
- planning does not appear after completion
- fetching does not appear before any search-stage work that produced candidate URLs
- synthesis does not appear before evidence gathering completes
- terminal success appears exactly once on successful runs
- terminal error appears exactly once on failed runs
- no stage appears after a terminal event

Fail examples:

- completion arrives before synthesis or fallback finishes
- events are duplicated in contradictory order
- stage names jump backward in ways the implementation cannot justify
- the stream emits only heartbeats and never real stage transitions

### Scenario 3 - Stage transitions are meaningful, not synthetic

Purpose:

Prove that the stream reflects real work rather than timer-based pseudo-progress.

Setup:

- force different execution shapes, such as a normal research run, a Vane-disabled run, and a degraded fallback run

Steps:

- run each request variant under streaming mode
- compare the event sequences

Validate:

- event sequences differ when code paths genuinely differ
- the Vane stage only appears when Vane is attempted
- fallback or warning stages appear only when a degraded path is actually used
- stage detail helps explain what the request is waiting on without exposing unstable internals

Fail examples:

- every request emits the same generic timer sequence regardless of backend path
- the stream shows Vane on requests where Vane was never attempted
- fallback events never appear even when logs show fallback happened
- stage messages are cosmetic and cannot be mapped back to code behavior

### Scenario 4 - Non-streaming research path still works

Purpose:

Prove that adding progress support does not break existing clients that expect a single JSON response.

Setup:

- use the same representative research query with streaming disabled or absent

Steps:

- send a normal `POST /research` request without the streaming selector
- compare the returned payload to pre-MP-05 expectations

Validate:

- status code remains successful on the happy path
- response body is normal JSON, not SSE data
- core response fields remain present: `query`, `mode`, `direct_answer`, `summary`, `findings`, `sources`, `diagnostics`, `timings`, `confidence`
- existing debug or legacy toggles still behave normally
- latency may change modestly, but the request still completes without needing an SSE client

Fail examples:

- non-streaming clients now receive SSE framing
- response payload shape changes incompatibly without contract updates
- adding streaming causes the non-streaming path to hang or omit final fields

### Scenario 5 - Quick search remains a no-progress path

Purpose:

Prove that `/search` still behaves like quick retrieval and does not inherit research streaming semantics accidentally.

Setup:

- pick a simple factual quick-search query
- test both with and without any streaming-related headers or params to confirm they are ignored or rejected intentionally on `/search`

Steps:

- call `POST /search` with standard quick-search inputs
- observe headers, response type, latency, and payload shape

Validate:

- `/search` remains a normal request/response endpoint unless the implementation explicitly documents otherwise
- quick path latency stays within the expected low-latency budget for this milestone family
- response shape remains the Perplexity-style search result contract already used by `/search`
- quick queries do not emit research-stage payloads or require stage parsing

Fail examples:

- `/search` starts returning progress events or long-form research payloads
- quick search latency regresses because the endpoint now waits on research-only streaming code
- quick search semantics change without a deliberate contract decision

### Scenario 6 - Client disconnect is handled safely

Purpose:

Prove that an SSE client closing the connection early does not leave obviously unhealthy backend behavior.

Setup:

- use a streaming research request that takes long enough to disconnect mid-flight
- if possible, enable logs or hooks that can show cancellation and cleanup behavior

Steps:

- start the stream
- wait until at least one non-terminal progress event has arrived
- terminate the client connection abruptly
- inspect server behavior and any remaining work

Validate:

- server does not emit noisy unhandled exceptions for a normal client disconnect
- request-scoped resources are released in a timely way
- the backend either cancels remaining work or degrades to a bounded cleanup path consistent with implementation intent
- no orphaned infinite loops, hanging generators, or unbounded retries remain after disconnect
- later requests still succeed normally

Fail examples:

- disconnect leaves the worker stuck until process restart
- disconnect triggers stack traces for every cancelled request
- the backend continues expensive work indefinitely with no control path to stop
- the next request on the same process inherits broken state

### Scenario 7 - Streaming error path is explicit and ordered

Purpose:

Prove that streaming failure modes produce understandable events rather than silent socket closes.

Setup:

- force one of the long-running phases to fail, such as provider exhaustion, fetch failure burst, or Vane timeout

Steps:

- send a streaming research request
- record the stream until it terminates

Validate:

- warning or error events are emitted before termination when the implementation intends degraded completion
- terminal failure is explicit if the request cannot complete
- degraded but successful completion remains distinguishable from hard failure
- final ordering remains coherent even under error paths

Fail examples:

- stream closes with no final event and no explanation
- both success and error terminal events are emitted
- partial failure is invisible even though diagnostics show degradation

### Scenario 8 - Progress schema stays stable across representative runs

Purpose:

Prove that clients can rely on the event contract across normal and variant research requests.

Setup:

- collect streams for at least: standard research, multi-iteration research, degraded fallback, Vane-enabled success, and Vane-disabled success

Steps:

- diff event names and payload keys across runs
- compare against the documented schema

Validate:

- core event fields are present in every event type where required
- optional fields appear only when documented and meaningful
- field types remain stable across runs
- clients can parse all representative streams with the same parser

Fail examples:

- stage payload keys vary unpredictably by code path
- the same field flips between string and object across runs
- undocumented event types appear in ordinary successful requests

## Regression matrix

Run at least this matrix before marking MP-05 complete:

- long-running `POST /research` with streaming enabled and Vane enabled
- long-running `POST /research` with streaming enabled and Vane disabled
- long-running `POST /research` with streaming enabled and one forced degraded branch
- normal `POST /research` with streaming disabled
- quick `POST /search` happy path
- quick `POST /search` with provider fallback still functioning if that regression setup already exists
- one client-disconnect run on streaming research

For each run, record at minimum:

- request type
- streaming on or off
- observed event order or final payload type
- total latency
- warnings or errors
- pass or fail against expected behavior

## Manual validation checklist

Use this short operator checklist during end-to-end verification:

- can an SSE client connect and receive an early progress event quickly
- can the operator tell what broad stage the request is in from the stream alone
- do intermediate events continue during a genuinely long request
- does the stream end in one clear terminal state
- does the same query still work for non-streaming clients
- does `/search` still feel like quick search rather than streamed research
- does disconnecting a client avoid obviously unhealthy backend behavior

## Success criteria for "progress streaming works"

Declare MP-05 test validation successful only when all of the following are true:

- a long-running research request emits multiple ordered SSE events before final completion
- the event sequence includes real stage transitions rather than only keepalives or synthetic timers
- the stream contract is documented enough that a thin client can consume it without log scraping
- non-streaming `/research` clients still receive the expected final JSON contract
- `/search` remains a quick non-streaming path with no research-stage leakage
- disconnect handling is bounded and does not destabilize the service
- degraded and successful terminal outcomes are clearly distinguishable

## Blocking findings for review

Any of these should block MP-05 sign-off:

- streaming exists only as fake or timer-based pseudo-progress
- stage ordering cannot be trusted or differs arbitrarily across equivalent runs
- the SSE contract is too unstable for clients to parse reliably
- non-streaming `/research` breaks or changes contract unintentionally
- `/search` behavior regresses into streaming or heavy research semantics
- client disconnects create unbounded work, noisy crashes, or broken later requests

## Evidence to retain with the milestone

Keep these artifacts with the MP-05 validation record:

- one raw successful SSE capture
- one raw degraded SSE capture if applicable
- one normal non-streaming `/research` response sample
- one quick `/search` response sample
- any automated test output that asserts event order and schema stability
- brief notes on disconnect behavior and cleanup observations

These retained artifacts make later MP-06 wrapper work easier because the wrapper can be validated against an already-frozen backend progress contract.
