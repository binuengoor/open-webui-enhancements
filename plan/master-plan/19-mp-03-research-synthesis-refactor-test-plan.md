# MP-03 Research Synthesis Refactor Test Plan

This artifact is the concrete review and validation plan for MP-03.

It exists to validate that the research pipeline stops returning stitched excerpts and starts returning grounded synthesized answers while preserving clear evidence provenance.

It is intentionally test-focused.
It does not authorize product-surface changes outside the MP-03 synthesis scope defined in the master plan.

## Scope

This plan covers:

- MP-03 acceptance validation for grounded synthesis in `research` and `deep`
- separation between evidence gathering and final synthesis stages
- synthesized `direct_answer` versus snippet-style answer assembly
- synthesized `summary` quality and compression
- synthesized findings with clean citation mapping to evidence
- behavioral distinction between `research` and `deep`
- grounding quality versus fluency tradeoff checks
- review checks that keep `/search` semantics intact while research output changes

This plan does not cover:

- planner-schema validation work already covered by MP-02
- Vane reintegration repair intended for MP-04
- progress streaming intended for MP-05
- wrapper repair intended for MP-06
- broad benchmark automation intended for MP-08

## MP-03 acceptance anchor

MP-03 is only complete when all of the following are true:

- research answers read like answers, not pasted excerpts
- `direct_answer` is synthesized from evidence rather than assembled from snippets
- `summary` is a true compression of the grounded answer, not a count string or placeholder
- findings are synthesized and still cite supporting evidence cleanly
- citations still map back to concrete evidence without ambiguity
- `research` and `deep` differ enough in retrieval/synthesis behavior to justify both modes
- improved fluency does not come at the cost of weak grounding or invented claims

## Current implementation observations to validate

These observations come from the current docs and visible compiler behavior and should be verified, not assumed:

- the architecture and decision docs already require retrieval and synthesis to remain separate
- the current product defect is described as snippet assembly rather than grounded synthesis
- `ResultCompiler` already normalizes citation-bearing snippets for `/search`-style compilation, but that grounding layer is not yet equivalent to a final research synthesis stage
- README contract language already expects `direct_answer`, `summary`, `findings`, `citations`, and `sources`
- implementation guardrails already define failure conditions for copied snippets, low citations, ignored contradictions, and polished but weakly grounded answers
- citation minimum expectations are currently `research >= 5 usable citations` and `deep >= 8 usable citations` unless evidence is genuinely sparse
- mode mapping requires `/search` to stay quick and mostly retrieval-only even while research output becomes more synthesized

## Review targets

The MP-03 review should confirm or reject the following design targets before the milestone is marked complete.

### Target 1 - Evidence and synthesis are separate stages

Retrieval artifacts must be inputs to synthesis, not the response itself.

Validate that:

- evidence collection completes before final answer writing begins
- synthesis consumes normalized evidence objects rather than concatenated raw snippets
- answer-formatting code does not silently act as the synthesis layer
- diagnostics or code structure make the evidence-to-synthesis boundary inspectable
- failed or thin synthesis can be traced back to the underlying evidence set

Blocking findings include:

- response text is still primarily copied from snippet fields
- evidence extraction and final answer assembly are conflated in one opaque step
- no code path clearly distinguishes evidence objects from answer objects

### Target 2 - `direct_answer` is truly synthesized

`direct_answer` should answer the user question directly in grounded prose.

Validate that:

- `direct_answer` integrates multiple evidence items into one coherent answer
- it does not read like a stitched list of quoted fragments
- it does not merely restate the first finding or top citation
- strong claims in `direct_answer` are supportable from cited evidence
- contradiction or uncertainty is surfaced when evidence is mixed

Blocking findings include:

- `direct_answer` copies long spans from snippets or extracted pages
- `direct_answer` overstates certainty beyond what citations support
- `direct_answer` becomes more polished while citation support becomes thinner or vaguer

### Target 3 - `summary` is a real grounded summary

`summary` should compress the grounded answer faithfully.

Validate that:

- `summary` is shorter and more compressed than `direct_answer`
- it preserves the main conclusion and key caveats
- it avoids filler, boilerplate, and template language
- it remains grounded in the same evidence as the answer it summarizes
- it does not introduce claims absent from findings or `direct_answer`

Blocking findings include:

- `summary` is generic, placeholder-like, or empty
- `summary` adds claims not supported elsewhere in the response
- `summary` is just a label, count string, or shallow restatement of the query

### Target 4 - Citation mapping stays claim-level and auditable

Synthesis quality must not break provenance.

Validate that:

- every major finding has usable citation references
- citation ids map to actual evidence items or sources deterministically
- evidence spans remain attributable to cited material where supported by the contract
- contradictory findings can point to different supporting sources cleanly
- citation lists are neither empty decoration nor noisy duplication

Blocking findings include:

- synthesized claims cannot be traced back to evidence
- citation ids no longer align with actual evidence/source records
- findings cite sources at random or only attach one generic citation to everything

### Target 5 - `research` and `deep` have meaningful behavioral separation

Both modes should exist for a reason after synthesis is improved.

Validate that:

- `deep` explores more broadly or checks contradictions/gaps more aggressively than `research`
- `deep` typically returns richer evidence coverage or stronger caveat handling than `research`
- `research` remains bounded and useful without drifting into quick-search behavior
- mode differences can be explained from retrieval/synthesis budgets or response characteristics

Blocking findings include:

- `research` and `deep` now produce effectively identical behavior for representative hard queries
- `deep` only changes latency without improving coverage, caveats, or evidence quality
- `research` becomes so thin that only `deep` is genuinely useful

### Target 6 - Grounding wins over empty fluency

The pipeline must prefer accurate grounded answers over attractive unsupported prose.

Validate that:

- sparse evidence produces cautious synthesis rather than fabricated completeness
- contradictory evidence produces caveats or split conclusions rather than smooth false certainty
- lower-fluency but grounded output is preserved over polished unsupported wording
- quality gate logic rejects thin but fluent answers when grounding is weak

Blocking findings include:

- polished answers hide uncertainty or evidence gaps
- the system invents consensus when sources conflict
- low-citation outputs pass as successful because they sound good

## Test scenarios

Each scenario should record:

- query
- endpoint used
- request payload
- mode and depth settings
- evidence count before synthesis
- usable citation count after synthesis
- whether citations met milestone minimums or a sparse-evidence exception
- observed `direct_answer`, `summary`, and findings behavior
- whether claims were traceable to citations
- observed latency
- whether behavior matched expectation
- notes on grounding, fluency, contradictions, and residual risks

### Scenario 1 - Comparison query exposes snippet-assembly regressions

Goal:

- verify comparison answers are synthesized rather than pasted from result snippets

Example queries:

- `compare Claude 4 and GPT-5 for coding tasks`
- `compare Notion, Obsidian, and Logseq for research workflows`

Endpoint:

- `POST /research` with `depth=balanced`

Pass checks:

- `direct_answer` leads with a synthesized comparison, not a dump of excerpts
- findings group differences or tradeoffs instead of mirroring source wording line by line
- at least 5 usable citations exist unless evidence is genuinely sparse
- major comparison claims can be mapped to supporting evidence
- caveats appear where sources disagree or coverage is uneven

Fail checks:

- response reads like stacked snippets with light connective text
- one source dominates a multi-source comparison without justification
- comparison conclusions are not traceable to citations

### Scenario 2 - Broad explainer yields real summary compression

Goal:

- verify broad explainers return both a grounded answer and a faithful compressed summary

Example queries:

- `explain the main tradeoffs of passkeys versus passwords for typical users`
- `what changed in browser privacy sandbox proposals and why does it matter`

Endpoint:

- `POST /research` with `depth=balanced`

Pass checks:

- `direct_answer` explains the topic coherently using multiple sources
- `summary` is materially shorter than `direct_answer` and keeps the core conclusion
- `summary` keeps important caveats instead of flattening nuance
- findings add structured support rather than repeating the same prose verbatim

Fail checks:

- `summary` is generic, repetitive, or detached from the answer body
- the answer is readable but not actually supported by multiple citations
- findings simply restate the answer without evidence-specific support

### Scenario 3 - Contradiction-heavy topic surfaces disagreement honestly

Goal:

- verify synthesis does not erase conflicting evidence for the sake of fluency

Example queries:

- `do standing desks improve productivity and health outcomes`
- `are AI coding assistants increasing or decreasing developer productivity in practice`

Endpoint:

- `POST /research` with `depth=quality`

Pass checks:

- response identifies where evidence agrees and where it conflicts
- findings can attach different citations to competing claims
- `direct_answer` states the balance of evidence instead of a false binary conclusion
- `deep` behavior shows stronger caveat handling or contradiction checking than `research`
- at least 8 usable citations exist unless the topic is genuinely sparse

Fail checks:

- response collapses mixed evidence into a clean unsupported take
- contradictory studies or sources are omitted from the final synthesis
- `deep` gives no meaningful improvement over `research` on a contradiction-heavy query

### Scenario 4 - Sparse evidence topic prefers caution over polish

Goal:

- verify the synthesis layer stays honest when evidence is limited or recent

Example queries:

- `what is known so far about the newest open protocol for browser agents announced this month`
- `latest status of a niche database project with only a few credible writeups`

Endpoint:

- `POST /research` with `depth=balanced`

Pass checks:

- response explicitly states evidence sparsity, recency limits, or uncertainty
- citation count is lower only with a documented sparse-evidence rationale
- `summary` does not overclaim certainty beyond available support
- findings stay grounded in the few available sources

Fail checks:

- sparse evidence produces a smooth but weakly grounded answer
- unsupported extrapolations appear to make the output feel complete
- low citation coverage is not acknowledged

### Scenario 5 - Snippet-copy detection on findings and answer fields

Goal:

- force a direct review for excerpt-copy behavior

Setup ideas:

- compare response text against candidate snippets or extracted evidence spans
- inspect whether large n-gram overlap suggests copying rather than synthesis
- use at least one query whose source snippets are distinctive and easy to recognize

Example query:

- `summarize the current arguments for and against banning non-compete clauses in US tech employment`

Endpoint:

- `POST /research` with `depth=balanced`

Pass checks:

- answer fields paraphrase and integrate evidence rather than reproducing it wholesale
- quoted material is short, purposeful, and attributable when present
- findings do not simply mirror the evidence span text

Fail checks:

- multiple long phrases are copied directly from snippets without synthesis value
- findings are effectively excerpt buckets with citation tags
- only superficial wording changes separate the answer from source snippets

### Scenario 6 - Citation mapping integrity through synthesis

Goal:

- verify claim-to-citation links survive the new synthesis layer

Example query:

- `what are the main causes of recent bee population decline and which interventions have the strongest evidence`

Endpoint:

- `POST /research` with `depth=quality`

Pass checks:

- each major causal claim has supporting citations
- intervention claims cite the evidence that actually supports efficacy
- citations and sources resolve cleanly to identifiable evidence items
- if evidence spans are present, they are short and attributable to cited material

Fail checks:

- synthesized findings have citations that cannot be matched to the claim content
- citations are present in the payload but not useful for auditing claims
- the same broad citation is attached to unrelated findings indiscriminately

### Scenario 7 - `research` versus `deep` behavioral difference

Goal:

- verify mode separation is visible in the final product behavior, not just code intent

Example query:

- `investigate the strongest arguments for and against remote work mandates in software organizations`

Endpoints:

- `POST /research` with `depth=balanced`
- `POST /research` with `depth=quality`

Pass checks:

- `deep` shows broader evidence coverage, contradiction handling, or stronger caveat structure
- `research` remains coherent and well grounded but less exhaustive
- the difference is visible in citation breadth, findings richness, or explicit gap analysis
- both modes remain bounded and usable

Fail checks:

- outputs are effectively the same aside from wording or latency
- only one mode meets citation/grounding expectations
- `deep` adds verbosity without better evidence handling

### Scenario 8 - `/search` contract remains distinct

Goal:

- verify MP-03 does not leak research synthesis semantics into quick search

Example queries:

- `capital of japan`
- `OpenAI pricing API`

Endpoint:

- `POST /search`

Pass checks:

- response shape remains the quick-search contract
- `/search` does not expose research-style `direct_answer`, `summary`, or findings semantics unless already part of the explicit public contract for that endpoint
- latency and behavior remain aligned with MP-01 guardrails
- `/search` does not require the new synthesis path to remain useful

Fail checks:

- quick search starts behaving like long-form research
- MP-03 adds heavy synthesis dependency to `/search`
- endpoint boundaries become blurrier after the refactor

## Grounding-versus-fluency rubric

Use this rubric during review for `direct_answer`, `summary`, and findings.

### Score 0 - unsafe

Characteristics:

- strong claims with weak or missing citations
- contradictions flattened away
- obvious invented detail or unsupported certainty

Disposition:

- automatic fail

### Score 1 - grounded but weakly written

Characteristics:

- mostly supportable claims
- clunky prose or limited compression
- caveats present
- provenance still auditable

Disposition:

- acceptable if scope and usefulness are still adequate
- note synthesis-quality follow-ups, but do not fail only for style

### Score 2 - grounded and useful

Characteristics:

- coherent synthesis from multiple evidence items
- clear main answer and concise summary
- caveats or uncertainty where appropriate
- citation mapping remains easy to audit

Disposition:

- pass target for `research`

### Score 3 - grounded and strong

Characteristics:

- strong synthesis plus explicit handling of contradictions, uncertainty, or evidence gaps
- excellent compression and caveat discipline
- citation mapping remains clean despite higher fluency

Disposition:

- pass target for representative `deep` cases

Rule:

- a prettier answer must not outrank a more grounded answer when scoring pass/fail

## Suggested test levels

### Unit tests

Focus on:

- evidence normalization before synthesis
- citation-id preservation and normalization
- finding construction from evidence records
- snippet-copy safeguards if implemented
- sparse-evidence and contradiction handling helpers
- mode-specific output budget differences for `research` versus `deep`

Likely files:

- `app/services/compiler.py`
- `app/services/orchestrator.py`
- `app/models/contracts.py`
- any new synthesis or citation-mapping modules introduced during MP-03

### Integration tests

Focus on:

- `POST /research` balanced versus quality behavior
- response-field quality for `direct_answer`, `summary`, findings, citations, and sources
- citation minimum checks with sparse-evidence exceptions
- contradiction-heavy and broad-explainer end-to-end cases
- `/search` non-regression versus MP-01/MP-00 boundaries

Likely files:

- `app/main.py`
- `app/services/orchestrator.py`
- API route tests and research-flow tests if present or added

### Review-only checks

Focus on:

- code-path separation between evidence gathering and synthesis
- visible mode distinction between `research` and `deep`
- quality-gate rules matching the documented guardrails
- prevention of polished but weakly grounded outputs
- contract consistency with README and master-plan documents

## Required evidence to mark MP-03 done

Record at minimum:

- one comparison-query example showing synthesized `direct_answer`
- one broad-explainer example showing meaningful `summary` compression
- one contradiction-heavy example showing caveat-preserving synthesis
- one sparse-evidence example showing honesty over polish
- one audit of claim-to-citation mapping on findings
- one side-by-side `research` versus `deep` comparison on the same hard query
- evidence that `/search` remains distinct and does not depend on heavy synthesis
- pass/fail judgment against each MP-03 acceptance anchor item
- any blocking findings or residual risks

## Suggested residual-risk notes

Even if MP-03 passes, explicitly note whether any of the following remain true:

- citation mapping is adequate but still too coarse for per-claim auditing in harder cases
- `research` versus `deep` differences are present but still need calibration on a larger benchmark set
- sparse-evidence handling is safe but overly cautious or terse
- synthesis quality depends heavily on one model choice and needs robustness testing
- automated snippet-copy detection is still missing or weak

## Exit rule

Do not mark MP-03 complete merely because answer text becomes more polished.

Mark it complete only when:

- evidence gathering and synthesis are demonstrably separate
- `direct_answer` and `summary` are clearly synthesized
- findings are synthesized without breaking citation traceability
- `research` and `deep` differ meaningfully on representative hard queries
- grounding remains stronger than fluency pressure
- review finds no blocking regression in provenance, honesty, or endpoint boundaries
