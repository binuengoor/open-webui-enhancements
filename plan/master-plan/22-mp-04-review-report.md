# MP-04 Review Report

Date: 2026-04-21
Branch: `mp-04-vane-integration`
Reviewer: Codex subagent

## Scope reviewed

Validated the MP-04 changes against the test plan in `plan/master-plan/21-mp-04-vane-integration-test-plan.md` by:

- reading the acceptance criteria and current assumptions
- inspecting `git diff main..HEAD -- app/services/vane.py app/services/orchestrator.py`
- running `python3 -m compileall app`

## Result

MP-04 is mostly implemented as intended, but there is one material parsing gap that means the acceptance target is not fully satisfied yet.

## Findings

### 1. `vane.py` still does not explicitly parse `content`-shaped top-level or nested payloads

Status: **Fail / follow-up needed**

`app/services/vane.py` now does a much better job extracting text from several response shapes, including `answer`, `summary`, and `message`, and its recursive `_extract_text()` helper can read `content` once a selected path reaches a dict that contains it.

However, the actual lookup paths used for `answer` and `summary` do **not** include:

- top-level `content`
- `data.content`
- `result.content`

Current lookup paths only probe:

- `answer`
- `summary`
- `message`
- `data.answer`
- `data.summary`
- `data.message`
- `result.answer`
- `result.summary`
- `result.message`

That means a successful Vane payload shaped primarily as `content` can still be treated as empty unless the payload also includes one of the explicitly probed keys. This leaves the "answer/summary/message/content" acceptance requirement only partially met.

## Validated passes

### Orchestrator promotion behavior

Status: **Pass**

`app/services/orchestrator.py` now promotes successful Vane output into the main response surface:

- `_merge_vane_synthesis()` copies Vane `answer` into `direct_answer`
- it copies Vane `summary` or `message` into `summary`
- for `research` mode, it derives a summary from `answer` if summary text is absent

This is no longer limited to `legacy.deep_synthesis`.

### Merge-on-success behavior

Status: **Pass**

The orchestrator only calls `_merge_vane_synthesis()` inside the non-error branch:

- on Vane error, it appends a warning
- on success, it promotes the fields

So failed Vane calls do not overwrite the grounded response.

### Diagnostic exposure

Status: **Pass**

The response diagnostics now expose:

- `runtime.vane_timeout_s`
- `runtime.vane_optimization_mode`
- `diagnostics.synthesis.vane.used`
- `diagnostics.synthesis.vane.has_answer`
- `diagnostics.synthesis.vane.has_summary`
- `diagnostics.synthesis.vane.source_count`

This makes Vane usage and fallback state materially easier to verify.

### Timeout policy

Status: **Pass with minor caution**

The new timeout policy is sensible:

- `quick` is capped to a shorter timeout window
- `balanced` gets a moderate timeout floor
- `quality` gets the longest timeout floor

This matches the intended tradeoff between speed and answer quality. The only caution is that `quality` now enforces a minimum of 90 seconds, which may be longer than some deployment budgets, but the policy itself is internally consistent.

### Depth / optimization-mode selection

Status: **Pass**

The orchestrator now chooses Vane depth more intentionally:

- explicit `quick` and `quality` requests are preserved
- `deep` defaults to `balanced`
- `research` escalates to `quality` only for more complex queries, otherwise `balanced`

That is a sensible policy and avoids overusing the slowest mode by default.

## Verification notes

- `python3 -m compileall app` completed successfully.
- No implementation changes were made during this review.

## Overall assessment

MP-04 substantially improves backend Vane integration and fixes the major promotion issue in the orchestrator. The remaining gap is response parsing for `content`-shaped Vane payloads. Until that is addressed, I would treat MP-04 as **close but not fully complete** against the stated test-plan acceptance criteria.
