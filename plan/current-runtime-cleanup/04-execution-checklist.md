# Execution Checklist

## Phase 1: Remove unambiguous dead code ✅ DONE

- [x] orchestrator: remove broken `mode_budget` parameter/plumbing
- [x] orchestrator: remove unused `_infer_compat_mode()`
- [x] compiler.py: remove unused `compile_perplexity_results()`
- [x] compiler.py: remove unused `choose_search_profile()`
- [x] compiler.py: remove helpers only used by removed methods (`_build_prompt`, `_retry_for_json_only`, `_normalize_results`)
- [x] planner.py: remove unused `choose_mode()`
- [x] planner.py: remove unused `quick_profile()`
- [x] config.py: remove `PlannerConfig` class and `planner` field
- [x] config.py: remove `llm_fallback_enabled` from config
- [x] tests: remove `planner` stubs and `llm_fallback_enabled` assertions
- [x] commit: `0caad40` — Remove planner/compiler dead LLM-fallback config and dead code

## Phase 2: Trim orchestrator research synthesis cluster

- [x] Identify and remove Vane synthesis helpers (only used for old local research):
  - [ ] `_merge_vane_synthesis`
  - [ ] `_shape_vane_summaries`
  - [ ] `_normalize_vane_text`
  - [ ] `_assess_vane_answer`
  - [ ] `_looks_like_good_vane_summary`
  - [ ] `_is_truncation_like`
  - [ ] `_compress_summary_text`
  - [ ] `_condense_vane_text`
- [x] Identify and remove research vet/fallback helpers:
  - [ ] `_maybe_vet_and_fallback_research`
  - [ ] `_run_research_vet`
  - [ ] `_looks_useful`
  - [ ] `_suggest_fallback_query`
  - [ ] `_research_payload_from_perplexity`
- [ ] Identify and remove evidence extraction helpers (if not needed for concise search):
  - [ ] `_best_excerpt`
  - [ ] `_gather_evidence`
  - [ ] `_ensure_query_coverage`
  - [ ] `_build_citations`
  - [ ] `_build_findings`
  - [ ] `_clean_claim_text`
  - [ ] `_looks_like_boilerplate`
  - [ ] `_is_useful_claim_text`
  - [ ] `_claim_relevance_score`
  - [ ] `_query_terms`
  - [ ] `_paraphrase_terms`
  - [ ] `_looks_like_heading_or_title`
  - [ ] `_synthesize_claim`
  - [ ] `_build_sources`
  - [ ] `_citation_relevance_by_id`
  - [ ] `_supplemental_finding_for_subquestion`
  - [ ] `_findings_by_subquestion`
  - [ ] `_build_summary`
  - [ ] `_build_direct_answer`
  - [ ] `_mode_profile`
  - [ ] `_summarize_text`
  - [ ] `_common_terms`
  - [ ] `_confidence`
- [x] Audit: which evidence methods are still called by `/search` path vs only by old research path?
- [x] Remove Vane deep_search call from execute_search if research synthesis is removed
- [x] Remove VaneClient dependency from orchestrator if no longer needed
- [ ] Commit

## Phase 3: Consolidate Vane integration

- [x] Check if VaneClient is still used after Phase 2
- [x] If VaneClient is only used by `/research` proxy, migrate to proxy
- [x] Remove VaneClient dependency from orchestrator
- [x] Remove VaneClient import from main.py
- [x] Update tests that stub VaneClient
- [x] Remove app/services/vane.py if no callers remain
- [ ] Commit

## Phase 4: Simplify compat layer

- [x] searxng_compat.py: replace `_search_once` private reach-through with public helper
- [x] searxng_compat.py: replace `_slots` private access with public helper
- [x] Expose narrow public `search_rows()` or `run_provider_search()` on orchestrator
- [ ] Commit

## Phase 5: Contracts cleanup

- [x] Split `app/models/contracts.py` into:
  - [x] public API contracts (PerplexitySearchRequest/Response, ResearchRequest, SearxngCompatRequest/Response, FetchRequest, ExtractRequest)
  - [x] internal orchestration contracts (ProgressEvent, ProviderHealthRecord, ProviderResult, RoutingDecision, ResearchPlan)
- [x] Remove unused internal contracts if truly dead
- [x] Update imports across codebase
- [ ] Commit

## Phase 6: Stale config cleanup

- [x] Remove `service.request_timeout_s` if not consumed
- [x] Remove `routing.policy` if router doesn't use it (only logs it)
- [x] Remove related config file entries
- [ ] Update tests
- [ ] Commit

## Phase 7: Rename for clarity

- [ ] Consider renaming `ResearchOrchestrator` → `SearchService` or `WebsearchService`
- [ ] Consider renaming `execute_perplexity_search` → `execute_search_compat`
- [ ] Consider renaming `_search_once` → `_run_provider_search`
- [ ] Commit

## Phase 8: Validation

- [ ] Full compile check
- [ ] Run tests
- [ ] Live smoke test on `/search`, `/research`, `/compat/searxng`, `/fetch`, `/extract`
- [ ] MCP parity check
