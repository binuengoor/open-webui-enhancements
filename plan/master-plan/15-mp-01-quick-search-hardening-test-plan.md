# MP-01 Quick Search Hardening Test Plan

This artifact is the concrete test and review plan for MP-01.

It exists to validate that `POST /search` stays a Perplexity-compatible quick-search path while provider rotation, cooldown, and fallback behavior are hardened.

It is intentionally test-focused.
It does not authorize product-scope expansion for `/search`.

## Scope

This plan covers:

- `POST /search`
- Perplexity-compatible request and response shaping
- provider router rotation behavior in `app/providers/router.py`
- quick-search execution behavior in `app/services/orchestrator.py`
- review checks tied to MP-01 acceptance criteria

This plan does not cover:

- long-form `/research` quality
- deep research behavior
- wrapper repair beyond quick-search contract parity observations
- provider expansion work beyond current configured providers

## MP-01 acceptance anchor

MP-01 is only complete when all of the following are true:

- provider fallback is predictable
- response shape remains stable for `POST /search`
- latency stays within the quick-search budget
- `/search` remains retrieval-first and mostly non-LLM
- review finds no blocking regression that turns `/search` into disguised research

## Current implementation observations to validate

These observations come from the current code and should be verified, not assumed:

- `POST /search` accepts `PerplexitySearchRequest` and ignores unknown extra fields via `extra="ignore"`
- `/search` always builds an internal `SearchRequest` with `mode="fast"`
- `search_mode` changes source bias only through `_map_search_mode`; it does not select research or deep behavior
- deprecated `/search` request field `mode` is accepted but logged as ignored
- the router uses weighted rotation with single-call de-duplication, cooldown skipping, and bounded attempts
- provider trace is recorded during internal search execution but is not part of the public `/search` response contract
- optional compiler assistance can refine quick-search result shaping; it must not redefine `/search` into a heavy path
- current quick-search execution still calls internal search/fetch/ranking stages, so latency and scope drift need explicit review

## Latency expectations

These are the MP-01 pass thresholds derived from the guardrails.

### Primary targets

- typical successful quick-search request: under `3s`
- degraded but acceptable request with fallback, cooldown skip, or one provider failure: under `6s`

### Failure conditions

Treat MP-01 as failing if any of the following become normal behavior:

- normal fact lookups routinely exceed `3s`
- fallback scenarios routinely exceed `6s`
- requests become slow because `/search` is doing research-like synthesis or too much page work
- provider issues surface as endpoint failure when another eligible provider should have answered

## Test scenarios

Each scenario should record:

- query
- request payload
- configured providers relevant to the run
- observed status code
- observed total latency
- result count
- whether response shape matched contract
- whether fallback/cooldown behavior matched expectation
- notes on warnings, logs, or provider health state

### Scenario 1 - Baseline simple factual lookup

Goal:

- verify the common path is fast and boring

Example queries:

- `capital of canada`
- `python current stable version`
- `who is the CEO of Microsoft`

Request shape:

- minimal `POST /search` payload with `query` and default `max_results`

Pass checks:

- HTTP `200`
- response contains `id` and `results`
- each result item has `title`, `url`, and `snippet`
- result count is between `1` and requested `max_results`
- no research-style fields appear in the public response
- latency is under `3s` in a healthy environment

Fail checks:

- response shape drifts from `PerplexitySearchResponse`
- empty result set without corresponding provider-wide outage evidence
- request takes over `3s` without provider trouble
- output resembles long-form synthesized research instead of concise result cards

### Scenario 2 - Provider hard failure fallback

Goal:

- verify a provider exception does not break `/search` when another provider is eligible

Setup ideas:

- disable one provider endpoint temporarily
- point one configured provider at an invalid local target in a controlled test environment
- simulate provider error in a test double if a harness exists

Example query:

- `latest FastAPI release notes`

Pass checks:

- `/search` still returns HTTP `200` when a later provider succeeds
- latency stays under `6s`
- provider health shows failure tracking on the broken provider
- logs or diagnostics show failed attempt followed by later success
- no duplicate attempts against the same provider within one routed search call

Fail checks:

- endpoint returns 5xx despite another ready provider existing
- same provider is retried repeatedly within one request
- fallback only works by escalating `/search` into research behavior

### Scenario 3 - Rate-limit and cooldown path

Goal:

- verify rate-limited providers enter cooldown and are skipped predictably

Setup ideas:

- use a provider stub that returns `429`
- run against a known quota-limited provider in a safe local test window

Example query:

- `OpenAI pricing API`

Pass checks:

- first affected request records rate-limit handling
- provider health shows non-zero `cooldown_until` for the rate-limited provider
- subsequent request during cooldown skips that provider
- another eligible provider is tried if available
- degraded latency stays under `6s`

Fail checks:

- cooldown is not applied after rate limit
- cooled-down provider is still attempted immediately on the next request
- all requests fail even though another provider is ready

### Scenario 4 - Consecutive failure threshold cooldown

Goal:

- verify non-rate-limit failures eventually trip cooldown according to router policy

Setup ideas:

- induce repeat `ProviderError` from one provider
- inspect provider health before and after crossing the threshold

Example query:

- `site reliability engineering principles`

Pass checks:

- consecutive failures increment on each failed request
- cooldown is applied only after threshold is met for ordinary failures
- a later successful call resets consecutive failure count for the recovered provider

Fail checks:

- ordinary failures never trigger cooldown
- success does not clear consecutive failure state
- cooldown behavior is inconsistent with configured threshold

### Scenario 5 - Recency-sensitive quick search

Goal:

- verify quick-search recency knobs stay compatible and bounded

Example query:

- `US CPI latest release`

Example payload fields:

- `search_recency_filter=day`
- optionally `search_after_date_filter` for tighter windows

Pass checks:

- HTTP `200`
- response still uses the concise result contract
- obviously stale results are reduced relative to unfiltered baseline when recent sources exist
- latency remains under `3s` healthy, under `6s` degraded

Review checks:

- recency filtering does not require research-mode orchestration
- contract drift around wrapper-only fields is documented if observed

### Scenario 6 - Filter-bearing query

Goal:

- verify language/domain filters remain compatible and do not break fallback

Example query:

- `site:docs.python.org dataclasses tutorial`

Example payload fields:

- `search_domain_filter=["docs.python.org"]`
- `search_language_filter=["en"]`

Pass checks:

- HTTP `200`
- returned URLs respect the domain filter when enough matching results exist
- language filter does not crash or empty the response unexpectedly
- fallback behavior still works when the first provider cannot satisfy the query well

Fail checks:

- filters are silently ignored in final results without explanation
- filter-bearing requests exceed degraded latency budget routinely
- filter-bearing path changes response schema

### Scenario 7 - Deprecated mode compatibility

Goal:

- verify deprecated `mode` is accepted but does not alter `/search` semantics

Example payload:

- `{"query":"golang generics release", "mode":"deep"}`

Pass checks:

- request succeeds if otherwise valid
- output remains concise quick-search output
- logs indicate deprecated mode was ignored
- latency remains in quick-search budget

Fail checks:

- deprecated `mode` changes `/search` into research or deep behavior
- deprecated `mode` is rejected in a way that breaks compatibility without an explicit contract change

### Scenario 8 - Multi-query input dedupe and cap behavior

Goal:

- verify multi-query requests remain stable and respect `max_results`

Example payload:

- `query=["NVIDIA earnings date", "NVIDIA quarterly earnings date"]`
- `max_results=5`

Pass checks:

- HTTP `200`
- response result count does not exceed `max_results`
- duplicate URLs are deduplicated in the final result list
- latency stays within degraded budget

Fail checks:

- duplicate URLs dominate results
- max result cap is violated
- multi-query path multiplies latency enough to break the quick budget under normal conditions

## Public contract pass/fail checks

For every MP-01 scenario, verify the public `/search` contract remains stable.

### Required response shape

Response must remain compatible with:

- top-level `id`
- top-level `results`
- optional `server_time` only when requested

Each result must remain compatible with:

- `title`
- `url`
- `snippet`
- optional metadata fields already defined in `PerplexitySearchResult`

### Contract failures

Treat any of the following as blocking:

- `/search` returns internal `SearchResponse` fields like `direct_answer`, `summary`, `diagnostics`, or `citations`
- result items lose required Perplexity-style fields
- response shape changes between healthy and fallback cases in a way callers must special-case
- `/search` starts exposing research/deep semantics through public fields without a planned contract change

## Router and provider review criteria

The code review portion of MP-01 should explicitly inspect `app/providers/router.py` and `/search` orchestration call sites.

### Router review checklist

Confirm all of the following:

- weighted rotation is preserved across requests
- one routed search call does not try the same provider multiple times
- cooldown state is checked before attempting a provider
- rate-limit failures trigger cooldown immediately
- ordinary failures increment consecutive failure count
- ordinary failures trigger cooldown once threshold is crossed
- success resets consecutive failure count and clears stale failure reason
- routed search stops once non-empty rows are returned
- empty final result with provider trace is possible and handled gracefully by caller

### `/search` path review checklist

Confirm all of the following:

- `/search` always forces internal fast path semantics
- `search_mode` only biases provider/source behavior
- deprecated `mode` is ignored for orchestration depth
- unknown extra request fields are ignored, not treated as hidden behavior switches
- optional compiler use is bounded and does not become mandatory for useful results
- public `/search` output remains concise even if internal diagnostics are richer
- provider issues do not leak as contract-breaking responses

## Non-goal enforcement review

MP-01 review must fail if quick search drifts into forbidden `/search` behavior.

Blocking review findings include:

- `/search` depends on heavy synthesis to feel usable
- `/search` requires a planner for normal operation instead of treating planner help as optional fallback
- `/search` routinely fetches or compiles so much data that it behaves like research
- `/search` behavior becomes materially different based on wrapper/client quirks rather than backend contract
- provider hardening work changes the meaning of `/search` instead of making it more reliable

## Suggested fixture query set

Use this lightweight set for repeatable spot checks.

| Bucket | Query | Purpose |
|---|---|---|
| baseline fact | `capital of canada` | common low-latency fact lookup |
| technical fact | `python current stable version` | docs-heavy concise query |
| recent event | `US CPI latest release` | recency-sensitive fast path |
| product/docs | `site:docs.python.org dataclasses tutorial` | domain filter behavior |
| provider stress | `OpenAI pricing API` | likely to surface provider variance |
| compatibility | `golang generics release` with deprecated `mode=deep` | contract compatibility |
| multi-query | `NVIDIA earnings date` plus close paraphrase | dedupe and cap behavior |

## Evidence to record for milestone review

Record at least the following per scenario run:

- timestamp
- branch or commit under test
- request payload
- status code
- latency in milliseconds
- result count
- provider used or observed fallback path
- provider health snapshot if failure or cooldown was involved
- pass/fail against scenario expectations
- blocking notes or follow-up questions

## Exit criteria for this artifact

This artifact is satisfied only when someone can use it to perform an MP-01 review and answer all of these questions clearly:

- Is `/search` still Perplexity-compatible?
- Does provider fallback work predictably under failure and rate limit conditions?
- Does cooldown behavior match router policy?
- Does latency stay inside the quick-search budget?
- Did quick search remain retrieval-first rather than turning into hidden research?
