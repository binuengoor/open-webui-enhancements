# MP-03 Review Report

## Validation scope

Reviewed `main..HEAD` for:

- `app/services/orchestrator.py`
- `app/services/ranking.py`
- `app/services/compiler.py`
- `app/models/contracts.py`

Also ran:

- `python3 -m compileall app`

Result: compile step passed.

## Acceptance review

### 1. Evidence gathering before synthesis

Status: Pass

Evidence:

- `execute_search()` now calls `_gather_evidence()` immediately after page fetch and before `_build_summary()` / `_build_direct_answer()` in `app/services/orchestrator.py`.
- `_gather_evidence()` returns a structured bundle with `citations`, `findings`, `sources`, and `diagnostics`, which makes the evidence-to-synthesis boundary inspectable.
- `SearchDiagnostics` now includes `synthesis` metadata via `app/models/contracts.py`.

Assessment:

This satisfies the architectural separation target better than the prior snippet-assembly flow.

### 2. Findings are synthesized rather than raw excerpts

Status: Partial pass / concern

Evidence:

- `_build_findings()` now clusters citations via `self.ranker.cluster_citations(query, citations)` and emits one claim per cluster.
- `cluster_citations()` is implemented in `app/services/ranking.py`.
- `_synthesize_claim()` adds light synthesis by summarizing the lead excerpt and optionally appending corroboration or recurring terms.

Concern:

- The synthesized claim is still anchored almost entirely on the first excerpt in each cluster (`lead_excerpt = self._summarize_text(excerpts[0])`).
- In practice this means the output is closer to lightly rewritten excerpt compression than true multi-source claim synthesis.
- This may fall short of the acceptance bar that research answers should read like answers, not stitched excerpts.

### 3. Summary differs by mode

Status: Pass

Evidence:

- `_build_summary()` has separate mode handling for `research` and `deep` in `app/services/orchestrator.py`.
- `research` mentions additional grounded findings.
- `deep` uses different wording focused on tradeoffs and corroborating context.

Assessment:

The implementation clearly distinguishes `research` and `deep` summary text.

### 4. Direct answer differs by mode

Status: Partial pass / concern

Evidence:

- `_build_direct_answer()` has explicit `research` behavior: first claim plus up to two supporting claims.
- Non-`research` modes return only the first claim.

Concern:

- `deep` does not get distinct answer construction; it currently shares the same path as `fast`.
- The test-plan criterion asks whether direct answer differs by mode, especially `research` vs `deep`. That distinction is currently weak: only `research` is special-cased.

### 5. citation_clusters / citation_ids usage

Status: Mostly pass

Evidence:

- Citation clusters are created in `_build_findings()` and converted into `citation_ids` lists using source citation `id` values.
- `Finding.citation_ids` remains aligned with the response contract in `app/models/contracts.py`.
- Source/citation provenance is preserved cleanly enough for findings to map back to evidence.

Concern:

- There is no `cite_ids` field anywhere in the implementation. If the intended contract was literally `cite_ids`, that requirement is not implemented.
- If the test-plan wording meant `citation_ids`, then the implementation is consistent.

### 6. `cluster_citations()` implementation

Status: Pass

Evidence:

- Implemented in `app/services/ranking.py`.
- It groups citations by the first non-query token found in title/excerpt/source text, then sorts clusters by max relevance.

Concern:

- The heuristic is simple and brittle. Clusters may be driven by incidental tokens rather than actual semantic claims.
- This is acceptable as an initial implementation, but it is a quality risk for true synthesis.

### 7. Compiler instruction for synthesized snippets

Status: Pass

Evidence:

- `app/services/compiler.py` now includes: `Prefer synthesized, claim-like snippets over copied excerpts when possible.`

## Obvious regressions or missing pieces

### Finding 1 - `deep` mode synthesis remains only weakly distinct from non-research output

Severity: Medium

References:

- `app/services/orchestrator.py` `_build_direct_answer()`
- `app/services/orchestrator.py` `_build_summary()`

Why it matters:

The test plan requires behavior distinction between `research` and `deep` sufficient to justify both modes. Summary wording differs, but direct-answer construction does not meaningfully distinguish `deep` from `fast`. This leaves the mode split underpowered at the answer layer.

### Finding 2 - Claim synthesis still depends on the first excerpt in each cluster

Severity: Medium

References:

- `app/services/orchestrator.py` `_synthesize_claim()`

Why it matters:

The milestone goal is to stop returning stitched excerpts and produce grounded synthesized answers. `_synthesize_claim()` still derives the core sentence from `excerpts[0]`, then appends a generic corroboration phrase. That is an improvement over raw excerpt passthrough, but it does not fully satisfy the stated acceptance anchor for grounded synthesis.

### Finding 3 - Clustering heuristic is token-based rather than claim-based

Severity: Low to medium

References:

- `app/services/ranking.py` `cluster_citations()`

Why it matters:

Using the first non-query token as the grouping key can overcluster unrelated citations or split related ones based on wording differences. This risks unstable findings and weaker citation grouping quality, especially for nuanced research questions.

## Overall verdict

MP-03 is directionally improved and passes several structural checks:

- evidence gathering is clearly separated from final answer construction
- findings now come from clustered citation groups rather than one raw excerpt per citation
- summary mode handling exists
- compiler guidance now prefers synthesized snippets
- contracts expose synthesis diagnostics

However, I would not mark the milestone fully complete against the test plan yet.

Primary reasons:

- `deep` answer behavior is not sufficiently distinct from other non-research modes
- claim generation is still too excerpt-led to fully satisfy the "answers, not pasted excerpts" acceptance anchor
- clustering quality is heuristic enough to risk weak synthesis in real queries

## Command results

- `python3 -m compileall app` -> passed
