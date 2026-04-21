# Implementation Guardrails

This file exists to keep the implementation honest.

## 1. Acceptance thresholds

These are initial guardrails, not final benchmarks.

### Quick search

Target:

- typical latency under **3 seconds**

Degraded but still acceptable:

- under **6 seconds** when fallback or provider issues occur

Failure examples:

- requires heavy synthesis to feel useful
- routinely exceeds degraded budget without clear cause
- provider failure breaks the endpoint instead of falling back

### Research

Target:

- `research` mode: **30 to 45 seconds** typical upper bound
- `deep` mode: **90 to 180 seconds** typical upper bound

Failure examples:

- long runtime with no meaningful synthesis improvement
- empty or generic answer after long execution
- no progress visibility during slow execution

### Citation minimums

Initial minimum expectations:

- `research`: at least **5 usable citations** unless the topic is genuinely sparse
- `deep`: at least **8 usable citations** unless the topic is genuinely sparse

A usable citation means:

- relevant to the answer
- not obviously duplicate noise
- attributable to a source that can be named or linked cleanly

### Quality gate failure conditions

A final answer should fail the quality gate when any of the following are true:

- it is generic or placeholder-like
- it has too few grounded citations
- it copies snippets instead of synthesizing them
- it ignores obvious contradictions in the evidence
- it has strong claims with weak grounding
- it returns a polished answer with no meaningful source support

## 2. Open WebUI wrapper diagnosis is mandatory pre-work

Before implementation begins, a short artifact must exist documenting:

- why MCP currently works better than the Open WebUI wrapper
- where the wrapper diverges from backend contract behavior
- what the wrapper should and should not be responsible for

Rule:

- do not guess about the wrapper failure mode
- diagnose it explicitly first

## 3. Tool selection guidance

This must remain simple.

### Use `search` when:

- the task is a lightweight fact lookup
- the frontend agent can interpret the results directly
- low latency matters more than deep synthesis

### Use `fetch` / `extract` when:

- the agent needs to inspect one or a few pages directly
- the agent needs page structure or raw page content

### Use `research` when:

- the task needs multi-source synthesis
- citations matter
- the frontend agent would otherwise need multiple search/fetch cycles

### Use `deep_research` when:

- the topic is broad, ambiguous, contradiction-heavy, or high-context
- more exhaustive exploration is worth the higher latency budget

Rule:

- do not use `research` for every small fact lookup
- do not use `deep_research` unless the complexity justifies it

## 4. Freeze mode mapping in one place

There must be one canonical mapping document for:

- internal backend mode names
- public `/research` mode names
- any Vane optimization mapping used behind the scenes

No synonym drift later.

## 5. Freeze `/search` non-goals

`/search` must explicitly remain:

- low-latency first
- retrieval-first
- mostly non-LLM
- not a hidden long-running research endpoint

`/search` should not:

- require a mandatory planner for normal operation
- run heavy synthesis by default
- become a disguised research loop

If a use case genuinely needs more, it should move to `/research`.
