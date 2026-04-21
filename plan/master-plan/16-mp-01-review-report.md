# MP-01 Review Report

Date: 2026-04-21
Branch: `mp-01-search-hardening-test`
Commits reviewed:
- `8419ea8` - Harden quick search provider fallback
- `12cb1c5` - commit all plan artifacts and test plan to branch

## What was reviewed

Reviewed against `plan/master-plan/15-mp-01-quick-search-hardening-test-plan.md`:

- `app/providers/router.py`
- `app/services/orchestrator.py`
- implementation diff from `main..HEAD` for the files above
- syntax validation via `python3 -m compileall app`

This review was static only. No live provider failure injection or end-to-end latency measurements were run in this pass.

## Major criteria

| Criterion | Result | Notes |
|---|---|---|
| MP-01 goals addressed | PASS | The change hardens fallback by continuing past empty provider responses and bounding provider ordinary-failure cooldown threshold to at least 1. It also caps per-query result fetch size in `/search` multi-query aggregation. |
| Safe, minimal, scoped | PASS WITH ISSUE | The diff is small and confined to router/orchestrator behavior, but one change introduces a regression risk around provider health accounting for empty results. |
| Regressions or new bugs introduced | FAIL | Empty result sets are now counted as provider failures and can trigger cooldown for a provider that is healthy but simply had no matches for the current query. |
| `/search` contract preserved | PASS | `/search` still forces internal `mode="fast"`, keeps deprecated `mode` ignored, continues to return `PerplexitySearchResponse`, and the new `limit_override` stays internal. |
| Low latency / mostly non-LLM / Perplexity-compatible | PASS WITH RISK | The `limit_override` reduction is aligned with latency control for multi-query requests. However, this review did not execute live timing scenarios, and optional compiler/profile calls remain on the path as before. |
| `/search` non-goals respected | PASS | The change does not expand `/search` into research/deep semantics and does not alter the public contract toward internal search/research fields. |

## Findings

### 1. Empty result sets now poison provider health state

Status: Blocking

Location:
- `app/providers/router.py:79`
- `app/providers/router.py:145`
- `app/providers/router.py:155`

Details:
- The new `_mark_empty_result()` path routes any zero-row search response into `_mark_failure(..., "empty_results")`.
- That increments `consecutive_failures` and can eventually place the provider into cooldown.
- In the quick-search contract, an empty result set for a specific query is not equivalent to provider failure. A provider may be healthy, reachable, and returning a valid empty response for that query/filter combination.
- This creates a regression risk where normal sparse queries, restrictive filters, or provider-specific coverage gaps gradually sideline an otherwise healthy provider.
- That behavior can distort router policy, alter fallback order over time, and make cooldown state reflect content-match variance rather than actual provider reliability.

Why this matters for MP-01:
- MP-01 requires predictable fallback and cooldown behavior.
- The test plan explicitly distinguishes provider hard failures/rate limits from ordinary result behavior.
- Treating empty results as failures is broader than the router review checklist and can cause misleading cooldowns unrelated to reliability.

Suggested disposition:
- Do not merge as-is without clarifying and adjusting the empty-result health policy.
- At minimum, empty responses should not increment failure counters unless there is explicit product agreement that they represent degraded-provider behavior under narrowly defined conditions.

## Additional observations

- `app/services/orchestrator.py:289` and `app/services/orchestrator.py:325` improve multi-query result budgeting by passing a shrinking `remaining_results` value into internal search and compiler ranking. This is aligned with Scenario 8 in the test plan and should reduce unnecessary work after earlier queries have already filled part of the requested result budget.
- `app/services/orchestrator.py:396` keeps the result cap internal by translating `limit_override` into the provider router `options["limit"]`, with no public contract changes.
- `app/providers/router.py:27` forces `failure_threshold >= 1`. This is probably defensive, but it silently changes semantics if configuration previously relied on `0` as a special value. I did not mark this blocking because no such config contract was established in the reviewed material.
- `python3 -m compileall app` completed successfully.

## Final recommendation

Recommendation: **merge with fixes**

Rationale:
- The branch makes useful, scoped improvements for fallback continuity and multi-query budgeting.
- However, the empty-result failure accounting in `app/providers/router.py` introduces a behavior regression that can make cooldown and provider health tracking inaccurate.
- Once empty-result handling is corrected or explicitly justified by product/router policy, the rest of the change set looks acceptable for MP-01.
