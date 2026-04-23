# Code Cleanup Backlog

## Phase 1 — consolidate around the current architecture

1. Refactor `app/services/orchestrator.py`
   - keep only concise search, fetch/extract, metrics, and run-history behavior
   - delete research-only synthesis code paths and stale legacy helpers
   - consider renaming `ResearchOrchestrator` to match its real job

2. Consolidate Vane integration
   - keep `app/services/research_proxy.py` as the canonical Vane path
   - retire `app/services/vane.py` if no live caller still needs it
   - if shared mapping helpers remain useful, extract only the tiny shared pieces

3. Remove dead optional-LLM plumbing
   - reassess `app/services/compiler.py`
   - reassess `app/services/planner.py`
   - delete or narrow them to only what `/search` still truly uses

## Phase 2 — simplify contracts and naming

4. Split `app/models/contracts.py`
   - separate search contracts, research relay contracts, compat contracts, fetch/extract contracts, and ops contracts
   - remove obsolete research-engine-only models if unused

5. Reduce compatibility drag
   - reassess `/internal/search`
   - keep only if a real caller exists; otherwise remove it

## Phase 3 — tighten docs and validation

6. Keep docs aligned to runtime reality
   - README
   - compose comments
   - MCP descriptions

7. Keep tests aligned to active surfaces
   - `/search`
   - `/research` streaming passthrough
   - `/compat/searxng`
   - `/fetch`
   - `/extract`
   - MCP parity

## Cleanup priority

Highest-value cleanup target: `app/services/orchestrator.py`
