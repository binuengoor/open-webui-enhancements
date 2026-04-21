# Known-Good Direct Vane Config

This is the canonical current baseline for direct Vane testing.

Use this note as the reference point for:

- backend Vane integration repair
- parity testing between direct Vane and backend-driven Vane
- future regression checks when model/provider settings change

## Current baseline

Based on current repo findings and prior validation notes, the best-known working direct Vane configuration is:

- chat provider id: `29a86a6a-721c-414f-bb0b-67a4f5a2d8fc` (LiteLLM Local)
- chat model key: `opencode-go/mimo-v2-omni`
- embedding provider id: `481e7ec6-873e-4e8d-ad58-e49b214d8729` (OpenAI)
- embedding model key: `text-embedding-3-small`

This pairing is the current baseline because existing plan notes report successful direct Vane runs across all three optimization modes with it.

## Known direct results

For the bee decline/interventions comparison query used in the Luna notes:

- `speed` succeeded in roughly 16 to 59 seconds
- `balanced` succeeded in roughly 39 seconds
- `quality` succeeded in roughly 130 seconds

Observed qualitative guidance from the current notes:

- `speed` is usable as a fast synthesis layer
- `balanced` is the strongest default direct Vane mode candidate
- `quality` is strongest for deliberate deep-research use but carries the highest latency

## Important caveat

This is a known-good direct Vane configuration, not proof that the backend integration is healthy.

Current findings in the repo indicate:

- direct Vane can return strong long-form output with this config
- the current backend `/research` path still does not surface that output correctly
- backend mismatch is therefore treated as an integration problem until proven otherwise

## Canonical direct Vane mode guidance

Use this mapping when evaluating direct Vane behavior:

| Direct Vane optimization mode | Intended role |
|---|---|
| `speed` | lightweight synthesis or quick research assist |
| `balanced` | default Vane-assisted research candidate |
| `quality` | deliberate deep-research only |

## Relationship to backend mapping

Current backend code in `app/services/vane.py` constructs the Vane request using:

- `optimizationMode`
- `chatModel.providerId`
- `chatModel.key`
- `embeddingModel.providerId`
- `embeddingModel.key`

It also maps research depth to optimization mode like this:

- `quick -> speed`
- `balanced -> VANE_DEFAULT_MODE` if valid, else `balanced`
- `quality -> quality`

When debugging backend parity, verify that the backend request matches the direct request semantics before blaming Vane itself.

## What is not yet proven

The current notes support this configuration as a practical baseline, but they do not yet prove:

- stability across a wider benchmark matrix
- equal reliability for every query class
- that these provider ids are portable across environments
- that this should be the permanent production default

## Operational rule

Until MP-04 is complete:

- treat this config as the reference baseline for direct comparisons
- do not treat backend Vane failures as proof that direct Vane is unusable
- do not hard-code long-term product decisions around this exact provider/model pairing without wider validation
