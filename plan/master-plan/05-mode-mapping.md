# Mode Mapping

This is the canonical mapping for public modes, internal backend modes, and Vane optimization modes.

If implementation details change later, update this file first and then align code and other docs to it.

## Purpose

Freeze one vocabulary for:

- public HTTP/API semantics
- internal backend orchestration semantics
- Vane optimization mapping used behind the scenes

Rule:

- do not introduce new mode synonyms unless this file is updated intentionally

## Public API surfaces

### `POST /search`

`/search` is the Perplexity-compatible quick-search surface.

Public behavior:

- always low-latency first
- retrieval-first
- concise result list output
- does not expose heavy research semantics

Accepted public knobs on `/search`:

- `search_mode`: `auto|web|academic|sec`
- deprecated `mode`: accepted for compatibility only, ignored for behavior selection

### `POST /research`

`/research` is the explicit long-form research surface.

Public behavior:

- multi-source synthesis
- citation-bearing research output
- bounded higher-latency execution

Accepted public knobs on `/research`:

- `source_mode`: `web|academia|social|all`
- `depth`: `quick|balanced|quality`

### `POST /internal/search`

`/internal/search` remains the internal escape hatch for callers that need direct backend mode control.

Accepted internal modes:

- `auto`
- `fast`
- `deep`
- `research`

## Canonical mapping

### Public quick-search mapping

`/search` does not map caller-visible requests onto internal `deep` or internal `research`.

Canonical mapping:

| Surface | Caller field | Allowed values | Backend meaning |
|---|---|---|---|
| `/search` | `search_mode` | `auto`, `web`, `academic`, `sec` | provider/source bias only |
| `/search` | deprecated `mode` | accepted but ignored | no mode-selection effect |

Interpretation:

- `search_mode` changes retrieval targeting, not orchestration depth
- `/search` stays on the quick-search path regardless of `search_mode`
- `/search` should be treated operationally as equivalent to the internal fast path

### Public research-to-internal mapping

| Public surface | Public value | Internal backend meaning | Notes |
|---|---|---|---|
| `/research` | `depth=quick` | `mode=research` with low-budget research execution | still long-form research, just lighter |
| `/research` | `depth=balanced` | `mode=research` with default research budget | default long-form research profile |
| `/research` | `depth=quality` | `mode=deep` or equivalent highest-budget research execution | reserved for deliberate high-latency work |

Interpretation:

- public `depth` is the stable external vocabulary for long-form research
- internal `mode` is orchestration vocabulary, not a public compatibility promise
- `quality` is the only public depth that should map to the highest-budget deep-research behavior

## Internal mode semantics

### `auto`

Use heuristic or later bounded planner selection to choose between quick-search and research behavior.

Canonical meaning:

- internal convenience mode only
- not a public semantic guarantee on `/research`

### `fast`

Quick-search mode.

Canonical meaning:

- low latency
- mostly retrieval-only
- minimal or no heavy synthesis
- the operational equivalent of the `/search` path

### `research`

Default long-form research orchestration mode.

Canonical meaning:

- bounded multi-source synthesis
- moderate latency budget
- optional Vane branch only if validated and healthy
- should back `/research` for `quick` and `balanced` public depths

### `deep`

Highest-budget research orchestration mode.

Canonical meaning:

- broader retrieval and exploration
- strongest synthesis budget
- explicit fallback behavior required
- should only be used for clearly justified high-complexity requests
- should back `/research` `quality`

## Vane optimization mapping

Current backend code in `app/services/vane.py` maps public/internal research depth to Vane optimization mode like this:

| Research depth passed to Vane client | Vane optimization mode |
|---|---|
| `quick` | `speed` |
| `balanced` | `VANE_DEFAULT_MODE` if valid, else `balanced` |
| `quality` | `quality` |
| anything else | `balanced` |

Canonical operating rule:

- `VANE_DEFAULT_MODE` may tune the balanced research path only
- `quick` must map to `speed`
- `quality` must map to `quality`
- no caller-facing API should expose raw Vane optimization names directly

## Recommended stable contract going forward

Freeze the product contract as:

- `/search` = concise quick-search only
- `/research depth=quick` = lighter long-form research
- `/research depth=balanced` = default long-form research
- `/research depth=quality` = deliberate deep research
- `/internal/search mode=fast|research|deep` = backend control plane vocabulary

## Non-goal for this file

This file freezes semantics and mapping only.

It does not promise that the current implementation fully matches the intended deep/research distinction yet. That implementation work belongs to later milestones.
