from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from collections import Counter
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from app.models.contracts import RunHistoryEntry
from urllib.parse import urlparse

from app.cache.memory_cache import InMemoryCache
from app.core.config import AppConfig
from app.eval.quality import looks_useful_search_response
from app.models.contracts import PerplexitySearchRequest, PerplexitySearchResponse, PerplexitySearchResult
from app.models.contracts import ProgressEvent, ResearchPlan, RoutingDecision, SearchDiagnostics, SearchRequest, SearchResponse
from app.providers.router import ProviderRouter
from app.services.fetcher import PageFetcher
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker
from app.services.compiler import ResultCompiler
from app.services.vane import VaneClient
from app.services.run_history import RecentRunHistory
from app.services.report_exporter import ReportExporter


logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class ResearchOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        router: ProviderRouter,
        search_cache: InMemoryCache,
        page_cache: InMemoryCache,
        fetcher: PageFetcher,
        planner: QueryPlanner,
        ranker: Ranker,
        vane: VaneClient,
        compiler: ResultCompiler,
        run_history: RecentRunHistory | None = None,
        report_exporter: ReportExporter | None = None,
        auto_export_research: bool = False,
    ):
        self.config = config
        self.router = router
        self.search_cache = search_cache
        self.page_cache = page_cache
        self.fetcher = fetcher
        self.planner = planner
        self.ranker = ranker
        self.vane = vane
        self.compiler = compiler
        self.run_history = run_history or RecentRunHistory()
        self.report_exporter = report_exporter
        self.auto_export_research = auto_export_research

    def ensure_research_llm_available(self) -> None:
        if not self.config.research_llm_ready:
            raise ValueError(self.config.research_llm_requirement_error)

    async def execute_search(
        self,
        req: SearchRequest,
        progress_callback: ProgressCallback | None = None,
        endpoint: str = "/research",
    ) -> SearchResponse:
        if endpoint == "/research" or req.mode in {"research", "deep"}:
            self.ensure_research_llm_available()

        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        routing_decision = RoutingDecision.model_validate(self.planner.build_route_decision(req.mode, req.query))
        selected_mode = routing_decision.selected_mode
        search_limits = self.config.search_limits

        logger.info(
            "event=search_start request_id=%s mode=%s query=%r source_mode=%s depth=%s max_iterations=%s include_citations=%s include_legacy=%s",
            request_id,
            selected_mode,
            req.query,
            req.source_mode,
            req.depth,
            req.max_iterations,
            req.include_citations,
            req.include_legacy,
        )

        research_plan = ResearchPlan.model_validate(
            self.planner.build_research_plan(req.query, selected_mode, req.max_iterations)
        )
        plan = [step.model_dump() for step in research_plan.steps]
        all_result_sets: List[List[Dict[str, Any]]] = []
        provider_trace: List[Dict[str, Any]] = []
        warnings: List[str] = []
        errors: List[str] = []
        followups: List[str] = []
        search_cache_state = {"hits": 0, "misses": 0}

        iterations = 1
        if selected_mode == "research":
            iterations = min(req.max_iterations, 4)

        seen_titles: List[str] = []
        for cycle in range(iterations):
            query_text = plan[min(cycle, len(plan) - 1)]["text"] if cycle < len(plan) else req.query
            await self._emit_progress(
                progress_callback,
                type="progress",
                state="search_started",
                request_id=request_id,
                mode=selected_mode,
                message=f"Running search cycle {cycle + 1} of {iterations}",
                cycle=cycle + 1,
                total_cycles=iterations,
            )
            if cycle > 0 and selected_mode == "research":
                query_text = self.planner.followup_query(req.query, seen_titles)
                plan.append({"step": "followup", "text": query_text, "purpose": "followup"})
                followups.append(query_text)

            logger.info(
                "event=search_cycle request_id=%s cycle=%s/%s mode=%s query=%r",
                request_id,
                cycle + 1,
                iterations,
                selected_mode,
                query_text,
            )

            rows, trace, cache_meta = await self._search_once(
                query=query_text,
                mode=selected_mode,
                source_mode=req.source_mode,
                depth=req.depth,
                max_attempts=search_limits.max_provider_attempts,
                request_id=request_id,
                limit_override=req.user_context.get("limit_override"),
            )
            provider_trace.extend(trace)
            search_cache_state["hits"] += cache_meta["hits"]
            search_cache_state["misses"] += cache_meta["misses"]

            if rows:
                all_result_sets.append(rows)
                seen_titles.extend([r.get("title", "") for r in rows])
            else:
                errors.append(f"No provider returned results for cycle {cycle + 1}")

            if selected_mode != "research":
                break

        fused = self.ranker.fuse(all_result_sets)
        diverse = self.ranker.diversity_filter(fused, search_limits.max_pages_to_fetch)

        await self._emit_progress(
            progress_callback,
            type="progress",
            state="evidence_gathering",
            request_id=request_id,
            mode=selected_mode,
            message="Fetching and ranking source pages",
            total_cycles=iterations,
        )
        pages = await self._fetch_pages(req.query, diverse)
        evidence = self._gather_evidence(req.query, pages, selected_mode)
        citations = evidence["citations"]
        findings = evidence["findings"]
        findings, coverage_notes = self._ensure_query_coverage(req.query, findings, citations, selected_mode)
        sources = self._build_sources(citations)
        summary = self._build_summary(req.query, findings, citations, selected_mode)
        direct_answer = self._build_direct_answer(req.query, findings, summary, selected_mode)
        body = direct_answer
        confidence = self._confidence(pages, errors)

        deep_synthesis = None
        vane_decision = {
            "used": False,
            "body_preserved": False,
            "accepted": False,
            "acceptance_reason": "not_attempted",
            "rejection_reason": "",
            "body_source": "local_synthesis",
        }
        await self._emit_progress(
            progress_callback,
            type="progress",
            state="synthesizing",
            request_id=request_id,
            mode=selected_mode,
            message="Synthesizing findings",
            total_cycles=iterations,
        )
        if selected_mode in {"deep", "research"}:
            await self._emit_progress(
                progress_callback,
                type="progress",
                state="vane",
                request_id=request_id,
                mode=selected_mode,
                message="Evaluating Vane synthesis",
                total_cycles=iterations,
            )
            vane_depth = self._select_vane_depth(req.query, req.depth, selected_mode)
            logger.info(
                "event=vane_selected request_id=%s mode=%s query=%r depth=%s selected_vane_depth=%s",
                request_id,
                selected_mode,
                req.query,
                req.depth,
                vane_depth,
            )
            deep_synthesis = await self.vane.deep_search(req.query, req.source_mode, vane_depth)
            vane_decision["used"] = bool(deep_synthesis and not deep_synthesis.get("error"))
            if deep_synthesis.get("error"):
                vane_decision["rejection_reason"] = "error"
                warnings.append(f"Vane unavailable: {deep_synthesis['error']}")
            else:
                body, direct_answer, summary, vane_decision = await self._merge_vane_synthesis(
                    query=req.query,
                    deep_synthesis=deep_synthesis,
                    direct_answer=direct_answer,
                    summary=summary,
                    body=body,
                    mode=selected_mode,
                )
                logger.info(
                    "event=vane_promoted request_id=%s mode=%s accepted=%s has_answer=%s has_summary=%s reason=%s",
                    request_id,
                    selected_mode,
                    vane_decision.get("accepted"),
                    bool(deep_synthesis.get("answer")),
                    bool(deep_synthesis.get("summary") or deep_synthesis.get("message")),
                    vane_decision.get("acceptance_reason") or vane_decision.get("rejection_reason"),
                )

        runtime = {
            "strict_runtime": req.strict_runtime,
            "provider_count": len(self.router.health_snapshot()),
            "vane_enabled": bool(self.config.vane.enabled),
            "vane_timeout_s": deep_synthesis.get("timeout_s") if isinstance(deep_synthesis, dict) else None,
            "vane_optimization_mode": deep_synthesis.get("optimization_mode") if isinstance(deep_synthesis, dict) else None,
        }

        diagnostics = SearchDiagnostics(
            warnings=warnings,
            errors=errors,
            runtime=runtime,
            routing_decision=routing_decision,
            research_plan=research_plan,
            query_plan=plan,
            iterations=iterations,
            coverage_notes=[
                f"fused_results={len(fused)}",
                f"fetched_pages={len(pages)}",
                *coverage_notes,
            ],
            search_count=len(plan),
            fetched_count=len(pages),
            ranked_passage_count=len(citations),
            provider_trace=provider_trace,
            cache={
                "search": search_cache_state,
                "page": self.page_cache.stats(),
            },
            synthesis={
                **evidence["diagnostics"],
                "vane": {
                    "used": vane_decision.get("used", False),
                    "accepted": vane_decision.get("accepted", False),
                    "body_preserved": vane_decision.get("body_preserved", False),
                    "acceptance_reason": vane_decision.get("acceptance_reason", ""),
                    "rejection_reason": vane_decision.get("rejection_reason", ""),
                    "body_source": vane_decision.get("body_source", "local_synthesis"),
                    "direct_answer_source": vane_decision.get("direct_answer_source", "local_synthesis"),
                    "summary_source": vane_decision.get("summary_source", "local_synthesis"),
                    "has_answer": bool(deep_synthesis.get("answer")) if isinstance(deep_synthesis, dict) else False,
                    "has_summary": bool((deep_synthesis.get("summary") or deep_synthesis.get("message"))) if isinstance(deep_synthesis, dict) else False,
                    "source_count": len(deep_synthesis.get("sources", [])) if isinstance(deep_synthesis, dict) else 0,
                },
                "fallback_used": False,
                "fallback_reason": "",
            },
        )

        payload: Dict[str, Any] = {
            "query": req.query,
            "mode": selected_mode,
            "direct_answer": direct_answer,
            "summary": summary,
            "body": body,
            "findings": findings,
            "citations": citations if req.include_citations else [],
            "sources": sources,
            "follow_up_queries": followups,
            "diagnostics": diagnostics.model_dump(),
            "timings": {"total_ms": int((time.perf_counter() - started) * 1000)},
            "confidence": confidence,
        }

        if selected_mode in {"deep", "research"}:
            vetted_payload = await self._maybe_vet_and_fallback_research(
                req=req,
                payload=payload,
                request_id=request_id,
                started=started,
                selected_mode=selected_mode,
                mode_budget=mode_budget,
                progress_callback=progress_callback,
            )
            if vetted_payload is not None:
                payload = vetted_payload

        if req.include_legacy:
            payload["legacy"] = {
                "results_ranked": fused,
                "results_scraped": pages,
                "deep_synthesis": deep_synthesis,
            }
            logger.info("event=legacy_block_included request_id=%s legacy_keys=%s", request_id, list(payload["legacy"].keys()))

        logger.info(
            "event=search_complete request_id=%s mode=%s query=%r citations=%s sources=%s confidence=%s total_ms=%s errors=%s warnings=%s",
            request_id,
            selected_mode,
            req.query,
            len(citations),
            len(sources),
            confidence,
            payload["timings"]["total_ms"],
            len(errors),
            len(warnings),
        )

        await self._emit_progress(
            progress_callback,
            type="complete",
            state="complete",
            request_id=request_id,
            mode=selected_mode,
            message="Search complete",
            total_cycles=iterations,
            timings=payload["timings"],
        )

        response = SearchResponse.model_validate(payload)
        self._record_run(
            endpoint=endpoint,
            query=req.query,
            mode=selected_mode,
            success=not bool(errors),
            citations_count=len(response.citations),
            sources_count=len(response.sources),
            confidence=response.confidence,
            warnings=warnings,
            errors=errors,
        )
        if self.auto_export_research and endpoint == "/research" and self.report_exporter is not None:
            try:
                self.report_exporter.export_research_report(response)
            except Exception as exc:
                logger.warning("event=report_auto_export_failed request_id=%s query=%r error=%s", request_id, req.query, exc)
        return response

    async def execute_perplexity_search(self, req: PerplexitySearchRequest) -> PerplexitySearchResponse:
        request_id = req.trace_id or uuid.uuid4().hex[:8]
        queries = self._normalize_queries(req.query)
        if not queries:
            raise ValueError("query is required")

        source_mode = self._map_search_mode(req.search_mode)
        results: List[PerplexitySearchResult] = []
        seen_urls: set[str] = set()
        total_results_budget = req.max_results

        if req.mode:
            logger.info(
                "event=deprecated_mode_ignored request_id=%s provided_mode=%s endpoint=/search",
                request_id,
                req.mode,
            )

        logger.info(
            "event=perplexity_search_start request_id=%s query_count=%s max_results=%s mode=%s search_mode=%s",
            request_id,
            len(queries),
            req.max_results,
            "fast",
            req.search_mode or "none",
        )

        for query in queries:
            quick = {
                "mode": "fast",
                "depth": self._select_depth(req),
                "max_iterations": self._select_iterations(req),
            }
            logger.info(
                "event=search_profile_selected request_id=%s source=deterministic depth=%s max_iterations=%s",
                request_id,
                quick["depth"],
                quick["max_iterations"],
            )

            remaining_results = max(1, total_results_budget - len(results))
            internal = SearchRequest(
                query=query,
                mode="fast",
                source_mode=source_mode,
                depth=str(quick["depth"]),
                max_iterations=int(quick["max_iterations"]),
                include_citations=True,
                include_debug=False,
                include_legacy=False,
                strict_runtime=False,
                user_context={
                    "client": req.client or "open-webui",
                    "trace_id": request_id,
                    "country": req.country,
                    "max_tokens": req.max_tokens,
                    "max_tokens_per_page": req.max_tokens_per_page,
                    "search_language_filter": req.search_language_filter or [],
                    "search_domain_filter": req.search_domain_filter or [],
                    "search_recency_filter": req.search_recency_filter,
                    "search_after_date_filter": req.search_after_date_filter,
                    "search_before_date_filter": req.search_before_date_filter,
                    "last_updated_after_filter": req.last_updated_after_filter,
                    "last_updated_before_filter": req.last_updated_before_filter,
                    "limit_override": remaining_results,
                },
            )
            response = await self.execute_search(internal, endpoint="/search")
            items = self._perplexity_results_from_response(response)
            if not items:
                # Empty /search items mean upstream retrieval failed or all candidates were filtered out.
                # Return a clear client-visible error instead of a misleading success-with-zero-results.
                details = "; ".join(response.diagnostics.errors or []) or "no usable upstream results"
                raise ValueError(f"search upstream failure: {details}")
            items = self._apply_perplexity_filters(items, req)
            if not items:
                raise ValueError("search upstream failure: all candidate results were filtered out")

            items = self._apply_token_limits(items, req.max_tokens_per_page, req.max_tokens)
            for item in items:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                results.append(item)
                if len(results) >= req.max_results:
                    break
            if len(results) >= req.max_results:
                break

        server_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if req.display_server_time else None
        return PerplexitySearchResponse(id=f"search_{request_id}", results=results[: req.max_results], server_time=server_time)

    async def fetch(self, url: str) -> Dict[str, Any]:
        cached = self.page_cache.get(f"fetch:{url}")
        if cached:
            logger.info("event=fetch_cache_hit url=%s", url)
            return cached

        logger.info("event=fetch_start url=%s", url)
        page = await self.fetcher.fetch(url)
        self.page_cache.set(f"fetch:{url}", page, self.config.cache.page_cache_ttl_s)
        logger.info(
            "event=fetch_complete url=%s title=%r source=%s error=%s",
            url,
            page.get("title"),
            page.get("source"),
            page.get("error"),
        )
        return page

    async def extract(self, url: str) -> Dict[str, Any]:
        logger.info("event=extract_start url=%s", url)
        result = await self.fetcher.extract(url)
        logger.info(
            "event=extract_complete url=%s title=%r headings=%s links=%s error=%s",
            url,
            result.get("title"),
            len(result.get("headings", [])),
            len(result.get("links", [])),
            result.get("error"),
        )
        return result

    def metrics(self) -> Dict[str, Any]:
        provider_snap = self.router.health_snapshot()
        recent = self.run_history.list(limit=50)
        return {
            "cache_search": self.search_cache.stats(),
            "cache_page": self.page_cache.stats(),
            "providers": {
                "count": len(provider_snap),
                "healthy": [p.name for p in provider_snap if p.enabled and p.cooldown_until == 0],
                "cooldown": [p.name for p in provider_snap if p.cooldown_until > 0],
                "degraded": [p.name for p in provider_snap if p.consecutive_failures > 0],
            },
            "recent_runs": {
                "total": len(recent),
                "success": sum(1 for r in recent if r.success),
                "failed": sum(1 for r in recent if not r.success),
            },
        }

    def recent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [entry.model_dump() for entry in self.run_history.list(limit)]

    def record_failed_run(self, endpoint: str, query: str, mode: str, errors: List[str]) -> None:
        self._record_run(
            endpoint=endpoint,
            query=query,
            mode=mode,
            success=False,
            citations_count=0,
            sources_count=0,
            confidence=None,
            warnings=[],
            errors=errors,
        )

    async def _search_once(
        self,
        query: str,
        mode: str,
        source_mode: str,
        depth: str,
        max_attempts: int,
        request_id: str,
        limit_override: Any = None,
        extra_options: Dict[str, Any] | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
        limit = self.config.search_limits.max_pages_to_fetch
        if isinstance(limit_override, int) and limit_override > 0:
            limit = min(limit, limit_override)

        options = {
            "limit": limit,
            "source_mode": source_mode,
            "depth": depth,
            "request_id": request_id,
            "mode": mode,  # MP-07: propagate mode so router applies mode-aware provider preferences
        }
        if extra_options:
            options.update({key: value for key, value in extra_options.items() if value is not None})

        key = self._cache_key(query, mode, options)
        cached = self.search_cache.get(key)
        if cached:
            logger.info("event=search_cache_hit request_id=%s mode=%s query=%r", request_id, mode, query)
            return cached, [{"provider": "cache", "status": "hit"}], {"hits": 1, "misses": 0}

        logger.info("event=search_cache_miss request_id=%s mode=%s query=%r", request_id, mode, query)
        rows, trace = await self.router.routed_search(query, options, max_attempts=max_attempts)
        ttl = self._ttl_for_query(query)
        if rows:
            self.search_cache.set(key, rows, ttl)
            logger.info(
                "event=search_cache_store request_id=%s mode=%s query=%r ttl=%s result_count=%s",
                request_id,
                mode,
                query,
                ttl,
                len(rows),
            )
        return rows, trace, {"hits": 0, "misses": 1}

    async def _fetch_pages(self, query: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pages: List[Dict[str, Any]] = []
        for row in rows:
            url = row.get("url", "")
            if not url:
                continue
            page = await self.fetch(url)
            content = page.get("content", "")
            page["quality_score"] = self.ranker.quality_score(query, row, content)
            page["snippet"] = row.get("snippet", "")
            page["provider"] = row.get("provider", "")
            pages.append(page)
        pages.sort(key=lambda x: x.get("quality_score", 0.0), reverse=True)
        return pages

    async def _maybe_vet_and_fallback_research(
        self,
        req: SearchRequest,
        payload: Dict[str, Any],
        request_id: str,
        started: float,
        selected_mode: str,
        mode_budget: Any,
        progress_callback: ProgressCallback | None = None,
    ) -> Dict[str, Any] | None:
        vane_info = payload.get("diagnostics", {}).get("synthesis", {}).get("vane", {})
        vane_accepted = bool(vane_info.get("accepted"))

        if vane_accepted:
            return None

        await self._emit_progress(
            progress_callback,
            type="progress",
            state="vetting",
            request_id=request_id,
            mode=selected_mode,
            message="Vetting research response quality",
        )
        if not self._looks_useful(payload):
            payload = await self._run_research_vet(req, payload, request_id)

        if self._looks_useful(payload):
            return None

        fallback_query = self._suggest_fallback_query(req.query, payload)
        payload.setdefault("diagnostics", {}).setdefault("synthesis", {})["fallback_reason"] = (
            vane_info.get("rejection_reason") or "quality_gate_rejected"
        )
        await self._emit_progress(
            progress_callback,
            type="progress",
            state="fallback",
            request_id=request_id,
            mode=selected_mode,
            message="Running grounded fallback synthesis",
        )
        logger.info(
            "event=research_fallback_triggered request_id=%s mode=%s query=%r fallback_query=%r",
            request_id,
            selected_mode,
            req.query,
            fallback_query,
        )

        fallback_request = PerplexitySearchRequest(
            query=fallback_query or req.query,
            max_results=max(5, min(10, search_limits.max_pages_to_fetch)),
            display_server_time=False,
            country=None,
            max_tokens=None,
            max_tokens_per_page=None,
            search_language_filter=None,
            search_domain_filter=None,
            search_recency_filter=None,
            search_after_date_filter=None,
            search_before_date_filter=None,
            last_updated_after_filter=None,
            last_updated_before_filter=None,
            search_mode=None,
            client="mcp" if payload.get("diagnostics", {}).get("runtime", {}).get("strict_runtime") else "backend-fallback",
            trace_id=request_id,
        )
        fallback_response = await self.execute_perplexity_search(fallback_request)
        fallback_payload = self._research_payload_from_perplexity(req, payload, fallback_response, request_id, started, fallback_query)
        return fallback_payload

    async def _run_research_vet(self, req: SearchRequest, payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        vet = await self.compiler.vet_research_response(
            query=req.query,
            response=payload,
            fallback_query=self._suggest_fallback_query(req.query, payload),
        )
        if not vet:
            return payload

        reason = vet.get("reason") or ""
        if reason:
            payload["diagnostics"]["warnings"].append(f"final_vet: {reason}")

        if not vet.get("useful", False):
            payload["diagnostics"]["warnings"].append("final_vet: response rejected by compiler")
            payload["diagnostics"]["coverage_notes"].append("final_vet_rejected=true")
            payload["diagnostics"]["coverage_notes"].append(f"final_vet_fallback_query={vet.get('fallback_query') or req.query}")

        return payload

    def _looks_useful(self, payload: Dict[str, Any]) -> bool:
        return looks_useful_search_response(payload)

    def _suggest_fallback_query(self, query: str, payload: Dict[str, Any]) -> str:
        vet_hint = str(payload.get("direct_answer", "")).strip()
        if vet_hint:
            titles = [item.get("title", "") for item in payload.get("citations", [])[:3] if isinstance(item, dict)]
            if titles:
                return f"{query} {' '.join(titles[:2])}"
        return self.planner.followup_query(query, [item.get("claim", "") for item in payload.get("findings", []) if isinstance(item, dict)])

    def _research_payload_from_perplexity(
        self,
        req: SearchRequest,
        original_payload: Dict[str, Any],
        fallback_response: PerplexitySearchResponse,
        request_id: str,
        started: float,
        fallback_query: str,
    ) -> Dict[str, Any]:
        citations: List[Dict[str, Any]] = []
        findings: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []

        for idx, result in enumerate(fallback_response.results, start=1):
            citations.append(
                {
                    "id": idx,
                    "title": result.title,
                    "url": result.url,
                    "source": urlparse(result.url).netloc or "search",
                    "excerpt": result.snippet,
                    "published_at": "",
                    "last_updated": result.last_updated,
                    "language": "en",
                    "relevance_score": float(result.confidence or 0.0),
                    "passage_id": f"fallback-{idx}",
                }
            )
            findings.append({"claim": self._summarize_text(result.snippet or result.title or result.url), "citation_ids": [idx]})
            sources.append({"title": result.title, "url": result.url, "source": urlparse(result.url).netloc or "search"})

        summary = (
            self._build_summary(req.query, findings, citations, "research")
            if citations
            else "Final vetting rejected the long-form response, and the fallback search returned no grounded results."
        )
        direct_answer = self._build_direct_answer(req.query, findings, summary, "research")

        diagnostics = dict(original_payload.get("diagnostics", {}))
        diagnostics.setdefault("warnings", [])
        diagnostics.setdefault("errors", [])
        diagnostics.setdefault("coverage_notes", [])
        diagnostics.setdefault("provider_trace", [])
        diagnostics["warnings"].append("research_fallback_search_used")
        diagnostics["coverage_notes"].append(f"fallback_query={fallback_query or req.query}")
        diagnostics["coverage_notes"].append(f"fallback_results={len(citations)}")
        diagnostics["provider_trace"].append({"provider": "fallback-search", "status": "used", "query": fallback_query or req.query})
        diagnostics.setdefault("synthesis", {})["fallback_used"] = True
        diagnostics.setdefault("synthesis", {})["fallback_reason"] = diagnostics.get("synthesis", {}).get("fallback_reason") or "vane_rejected_or_unavailable"
        diagnostics.setdefault("synthesis", {})["body_source"] = "fallback_synthesis"

        confidence = "medium" if citations else "low"
        payload = {
            "query": req.query,
            "mode": original_payload.get("mode", "research"),
            "direct_answer": direct_answer,
            "summary": summary,
            "body": direct_answer,
            "findings": findings,
            "citations": citations,
            "sources": sources,
            "follow_up_queries": [],
            "diagnostics": diagnostics,
            "timings": {"total_ms": int((time.perf_counter() - started) * 1000)},
            "confidence": confidence,
            "legacy": original_payload.get("legacy"),
        }
        payload["diagnostics"]["synthesis"].setdefault("vane", {})["body_preserved"] = False
        payload["diagnostics"]["synthesis"]["vane"]["accepted"] = False
        return payload

    def _normalize_queries(self, query: Any) -> List[str]:
        if isinstance(query, list):
            return [item.strip() for item in query if isinstance(item, str) and item.strip()]
        if isinstance(query, str) and query.strip():
            return [query.strip()]
        return []

    def _infer_compat_mode(self, req: PerplexitySearchRequest) -> str:
        if len(self._normalize_queries(req.query)) > 1:
            return "research"
        if req.search_recency_filter or (req.max_tokens and req.max_tokens >= 50000):
            return "research"
        if req.max_tokens_per_page and req.max_tokens_per_page >= 4096:
            return "deep"
        if req.search_domain_filter or req.search_language_filter or req.country or req.search_mode in {"academic", "sec"}:
            return "deep"
        if req.max_results <= 3:
            return "fast"
        return "auto"

    def _map_search_mode(self, search_mode: str | None) -> str:
        if search_mode == "academic":
            return "academia"
        if search_mode == "sec":
            return "all"
        return "web"

    def _select_depth(self, req: PerplexitySearchRequest) -> str:
        if req.search_mode == "academic":
            return "quality"
        if req.search_recency_filter:
            return "quick"
        if req.max_tokens_per_page and req.max_tokens_per_page >= 4096:
            return "quality"
        if req.max_tokens and req.max_tokens >= 50000:
            return "quality"
        if req.search_domain_filter or req.search_language_filter or req.country:
            return "balanced"
        if req.max_results > 8:
            return "balanced"
        return "quick"

    def _select_iterations(self, req: PerplexitySearchRequest) -> int:
        if req.search_mode == "academic":
            return 2
        if req.max_tokens and req.max_tokens >= 50000:
            return 4
        if req.max_tokens_per_page and req.max_tokens_per_page >= 4096:
            return 3
        if req.search_domain_filter or req.search_language_filter or req.country:
            return 2
        return 1

    def _perplexity_results_from_response(self, response: SearchResponse) -> List[PerplexitySearchResult]:
        citation_map: Dict[str, Dict[str, Any]] = {
            citation.url: citation.model_dump() if hasattr(citation, "model_dump") else dict(citation)
            for citation in response.citations
        }
        results: List[PerplexitySearchResult] = []
        for source in response.sources:
            url = source.url
            citation = citation_map.get(url, {})
            snippet = citation.get("excerpt") or response.summary or response.body or response.direct_answer or ""
            results.append(
                PerplexitySearchResult(
                    title=source.title or citation.get("title") or "Untitled",
                    url=url,
                    snippet=snippet,
                    date=citation.get("published_at") or None,
                    last_updated=citation.get("last_updated") or None,
                )
            )
        if not results and (response.body or response.direct_answer):
            results.append(
                PerplexitySearchResult(
                    title=response.query,
                    url="",
                    snippet=response.body or response.direct_answer,
                    date=None,
                    last_updated=None,
                )
            )
        return [item for item in results if item.url]

    def _apply_perplexity_filters(self, results: List[PerplexitySearchResult], req: PerplexitySearchRequest) -> List[PerplexitySearchResult]:
        filtered = self._filter_perplexity_results(results, req.search_domain_filter or [])
        filtered = self._filter_language_results(filtered, req.search_language_filter or [])
        filtered = self._filter_date_results(
            filtered,
            req.search_recency_filter,
            req.search_after_date_filter,
            req.search_before_date_filter,
            req.last_updated_after_filter,
            req.last_updated_before_filter,
        )
        return filtered

    def _filter_perplexity_results(self, results: List[PerplexitySearchResult], domain_filter: List[str]) -> List[PerplexitySearchResult]:
        if not domain_filter:
            return results

        allowlist = [domain.lstrip("-").lower() for domain in domain_filter if domain and not domain.startswith("-")]
        denylist = [domain[1:].lower() for domain in domain_filter if domain and domain.startswith("-")]

        filtered: List[PerplexitySearchResult] = []
        for item in results:
            host = urlparse(item.url).netloc.lower()
            if allowlist and not any(host.endswith(domain) for domain in allowlist):
                continue
            if any(host.endswith(domain) for domain in denylist):
                continue
            filtered.append(item)
        return filtered

    def _filter_language_results(self, results: List[PerplexitySearchResult], language_filter: List[str]) -> List[PerplexitySearchResult]:
        if not language_filter:
            return results

        allowed = {lang.lower().strip() for lang in language_filter if lang and len(lang.strip()) == 2}
        if not allowed:
            return results

        tld_map = {
            "fr": {"fr"},
            "de": {"de"},
            "es": {"es"},
            "it": {"it"},
            "pt": {"pt", "br"},
            "nl": {"nl"},
            "ja": {"jp"},
            "ko": {"kr"},
            "zh": {"cn", "hk", "tw"},
            "en": {"com", "org", "net", "io", "us", "uk", "ca", "au"},
        }

        filtered: List[PerplexitySearchResult] = []
        for item in results:
            host = urlparse(item.url).netloc.lower()
            tld = host.split(".")[-1] if "." in host else ""
            inferred_langs = {lang for lang, tlds in tld_map.items() if tld in tlds}
            if inferred_langs & allowed:
                filtered.append(item)
        return filtered if filtered else results

    def _filter_date_results(
        self,
        results: List[PerplexitySearchResult],
        recency: str | None,
        published_after: str | None,
        published_before: str | None,
        updated_after: str | None,
        updated_before: str | None,
    ) -> List[PerplexitySearchResult]:
        if not any([recency, published_after, published_before, updated_after, updated_before]):
            return results

        now = datetime.now(timezone.utc)
        recency_after = None
        if recency == "hour":
            recency_after = now - timedelta(hours=1)
        elif recency == "day":
            recency_after = now - timedelta(days=1)
        elif recency == "week":
            recency_after = now - timedelta(days=7)
        elif recency == "month":
            recency_after = now - timedelta(days=31)
        elif recency == "year":
            recency_after = now - timedelta(days=366)

        pub_after_dt = self._parse_date(published_after)
        pub_before_dt = self._parse_date(published_before)
        upd_after_dt = self._parse_date(updated_after)
        upd_before_dt = self._parse_date(updated_before)

        filtered: List[PerplexitySearchResult] = []
        for item in results:
            published_dt = self._parse_date(item.date)
            updated_dt = self._parse_date(item.last_updated)

            if recency_after and published_dt and published_dt < recency_after:
                continue
            if pub_after_dt and published_dt and published_dt < pub_after_dt:
                continue
            if pub_before_dt and published_dt and published_dt > pub_before_dt:
                continue
            if upd_after_dt and updated_dt and updated_dt < upd_after_dt:
                continue
            if upd_before_dt and updated_dt and updated_dt > upd_before_dt:
                continue
            filtered.append(item)

        return filtered

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            if "/" in text:
                return datetime.strptime(text, "%m/%d/%Y").replace(tzinfo=timezone.utc)
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _apply_token_limits(
        self,
        results: List[PerplexitySearchResult],
        max_tokens_per_page: int | None,
        max_tokens: int | None,
    ) -> List[PerplexitySearchResult]:
        capped: List[PerplexitySearchResult] = []
        remaining = max_tokens if max_tokens is not None else None

        per_page_limit = max_tokens_per_page or 0
        for item in results:
            snippet = item.snippet or ""
            if per_page_limit > 0:
                snippet = self._truncate_to_tokens(snippet, per_page_limit)

            token_count = self._estimate_tokens(snippet)
            if remaining is not None:
                if remaining <= 0:
                    break
                if token_count > remaining:
                    snippet = self._truncate_to_tokens(snippet, remaining)
                    token_count = self._estimate_tokens(snippet)
                remaining -= token_count

            capped.append(
                PerplexitySearchResult(
                    title=item.title,
                    url=item.url,
                    snippet=snippet,
                    date=item.date,
                    last_updated=item.last_updated,
                    citation_ids=item.citation_ids,
                    evidence_spans=item.evidence_spans,
                    confidence=item.confidence,
                    grounding_notes=item.grounding_notes,
                )
            )
        return capped

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text.split()) * 1.3))

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        words = text.split()
        if not words:
            return ""
        approx_words = max(1, int(max_tokens / 1.3))
        return " ".join(words[:approx_words])

    async def _emit_progress(
        self,
        progress_callback: ProgressCallback | None,
        **event: Any,
    ) -> None:
        if progress_callback is None:
            return
        await progress_callback(ProgressEvent.model_validate(event))

    def _cache_key(self, query: str, mode: str, options: Dict[str, Any]) -> str:
        body = f"{query.lower().strip()}|{mode}|{sorted(options.items())}"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:20]
        return f"search:{digest}"

    def _ttl_for_query(self, query: str) -> int:
        ql = query.lower()
        if re.search(r"\b(today|latest|current|recent|news|this week|this month)\b", ql):
            return self.config.cache.ttl_recency_s
        return self.config.cache.ttl_general_s

    def _best_excerpt(self, content: str, query: str) -> str:
        if not content:
            return ""

        candidates = [
            self._clean_claim_text(part)
            for part in re.split(r"(?<=[.!?])\s+|\n+", content)
        ]

        scored: List[tuple[float, str]] = []
        for sentence in candidates[:140]:
            score = self._claim_relevance_score(sentence, query, allow_question=False)
            if score <= 0:
                continue
            scored.append((score, sentence))

        if not scored:
            return ""

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1][:320]

    def _gather_evidence(self, query: str, pages: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
        citations = self._build_citations(query, pages)
        findings = self._build_findings(query, citations, mode)
        sources = self._build_sources(citations)
        return {
            "citations": citations,
            "findings": findings,
            "sources": sources,
            "diagnostics": {
                "stage": "evidence_gathering",
                "mode_profile": self._mode_profile(mode),
                "citation_count": len(citations),
                "finding_count": len(findings),
            },
        }

    def _ensure_query_coverage(
        self,
        query: str,
        findings: List[Dict[str, Any]],
        citations: List[Dict[str, Any]],
        mode: str,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if mode != "research":
            return findings, []

        subquestions = self.planner.decompose_query(query)
        if len(subquestions) <= 1:
            return findings, ["coverage_check=single_question"]

        notes = [f"coverage_check=subquestions:{len(subquestions)}"]
        augmented = list(findings)
        assigned_ids = {cid for finding in findings for cid in finding.get("citation_ids", [])}

        for subquestion in subquestions:
            matched = any(
                self._claim_relevance_score(finding.get("claim", ""), subquestion, allow_question=False) > 0
                for finding in augmented
            )
            if matched:
                notes.append(f"covered:{subquestion}")
                continue

            supplemental = self._supplemental_finding_for_subquestion(subquestion, citations, assigned_ids)
            if supplemental:
                augmented.append(supplemental)
                assigned_ids.update(supplemental.get("citation_ids", []))
                notes.append(f"supplemented:{subquestion}")
            else:
                notes.append(f"missing:{subquestion}")

        ranked = sorted(
            augmented,
            key=lambda finding: max(
                [self._citation_relevance_by_id(citations, cid) for cid in finding.get("citation_ids", [])] or [0.0]
            ),
            reverse=True,
        )
        return ranked, notes

    def _build_citations(self, query: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        citations = []
        for page in pages[:12]:
            url = page.get("url", "")
            parsed = urlparse(url)
            source = parsed.netloc.lower()
            title = self._clean_claim_text(page.get("title", "Untitled")) or "Untitled"
            excerpt = self._best_excerpt(page.get("content", ""), query)
            excerpt_score = self._claim_relevance_score(excerpt, query, allow_question=False) if excerpt else 0.0
            title_score = self._claim_relevance_score(title, query, allow_question=False)

            if excerpt_score <= 0:
                excerpt = self._clean_claim_text(page.get("snippet", ""))
                excerpt_score = self._claim_relevance_score(excerpt, query, allow_question=False)
            if excerpt_score <= 0:
                continue

            combined_score = max(float(page.get("quality_score", 0.0)), excerpt_score)
            if combined_score < 0.2 and title_score <= 0:
                continue

            citations.append(
                {
                    "id": len(citations) + 1,
                    "title": title,
                    "url": url,
                    "source": source,
                    "excerpt": excerpt,
                    "published_at": page.get("published_at", ""),
                    "last_updated": page.get("last_updated") or None,
                    "language": page.get("language") or None,
                    "relevance_score": round(combined_score, 3),
                    "passage_id": f"p{len(citations) + 1}-{re.sub(r'[^a-z0-9]+', '-', source)[:40] or 'source'}",
                }
            )
        return citations

    def _build_findings(self, query: str, citations: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
        findings = []
        seen_claims = set()
        strong_citations = [
            item for item in citations
            if self._claim_relevance_score(item.get("excerpt", ""), query, allow_question=False) > 0
        ]
        citation_clusters = self.ranker.cluster_citations(query, strong_citations)
        for cluster in citation_clusters[:6]:
            cluster = sorted(cluster, key=lambda item: item.get("relevance_score", 0.0), reverse=True)
            claim = self._synthesize_claim(query, cluster, mode)
            citation_ids = [item["id"] for item in cluster if item.get("id")]
            if not claim or not citation_ids:
                continue
            claim_key = re.sub(r"\bcorroborated .*", "", claim.lower()).strip()
            if claim_key in seen_claims:
                continue
            seen_claims.add(claim_key)
            findings.append({"claim": claim, "citation_ids": citation_ids})
        return findings

    def _clean_claim_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip(" ,;:-")
        cleaned = re.sub(r"^[\[\]{}()<>|]+|[\[\]{}()<>|]+$", "", cleaned)
        return cleaned

    def _looks_like_boilerplate(self, text: str) -> bool:
        lowered = text.lower()
        if lowered in {"context", "abstract", "overview", "introduction", "menu", "home"}:
            return True
        boilerplate_patterns = [
            r"please refer to the appropriate style manual",
            r"all rights reserved",
            r"cookie policy",
            r"privacy policy",
            r"terms of use",
            r"skip to (main )?content",
            r"subscribe to our",
            r"sign up for our",
            r"copyright",
            r"javascript is required",
        ]
        return any(re.search(pattern, lowered) for pattern in boilerplate_patterns)

    def _is_useful_claim_text(self, text: str, query: str, allow_question: bool = True) -> bool:
        return self._claim_relevance_score(text, query, allow_question=allow_question) > 0

    def _claim_relevance_score(self, text: str, query: str, allow_question: bool = True) -> float:
        cleaned = self._clean_claim_text(text)
        if len(cleaned) < 35:
            return 0.0
        if len(re.sub(r"[^A-Za-z0-9]", "", cleaned)) < 20:
            return 0.0
        if re.fullmatch(r"[.?!]+", cleaned):
            return 0.0
        if self._looks_like_boilerplate(cleaned):
            return 0.0
        if cleaned.endswith("?") and not allow_question:
            return 0.0
        if cleaned.endswith("..."):
            return 0.0

        lowered = cleaned.lower()
        if self._looks_like_heading_or_title(lowered):
            return 0.0

        query_terms = self._query_terms(query)
        if not query_terms:
            return 0.0
        text_terms = set(re.findall(r"[a-z0-9]+", lowered))
        overlap_terms = query_terms & text_terms
        overlap = len(overlap_terms)
        if overlap == 0:
            return 0.0

        junk_patterns = [
            r"^how many ",
            r"^what is ",
            r"^what are ",
            r"^who is ",
            r"^how do we know",
            r"^learn more",
            r"^read more",
            r"^click here",
            r"^related article",
            r"^share this",
            r"^photo by",
        ]
        if any(re.match(pattern, lowered) for pattern in junk_patterns):
            return 0.0

        if not re.search(r"[.!?]", cleaned) and len(cleaned.split()) < 10:
            return 0.0

        score = overlap / max(1, len(query_terms))
        if overlap >= min(2, len(query_terms)):
            score += 0.2
        if re.search(r"\b(is|are|was|were|can|may|will|shows|found|reported|observed|measured|estimated|because|through|by)\b", lowered):
            score += 0.15
        if any(char.isdigit() for char in cleaned):
            score += 0.05
        if cleaned.endswith("?"):
            score -= 0.2
        if len(overlap_terms) == 1 and len(query_terms) >= 3:
            score -= 0.15
        return max(0.0, round(score, 4))

    def _query_terms(self, query: str) -> set[str]:
        terms = {
            term
            for term in re.findall(r"[a-z0-9]+", query.lower())
            if len(term) > 2 and term not in {"about", "with", "from", "into", "their", "there", "which"}
        }
        expanded = set(terms)
        for term in list(terms):
            expanded.update(self._paraphrase_terms(term))
        return expanded

    def _paraphrase_terms(self, term: str) -> set[str]:
        aliases = {
            "blackholes": {"black", "hole", "holes", "blackhole", "blackholes"},
            "blackhole": {"black", "hole", "holes", "blackhole", "blackholes"},
            "form": {"form", "forms", "formed", "formation", "origins", "origin", "collapse"},
            "discovery": {"discovery", "discoveries", "finding", "findings", "result", "results", "evidence"},
            "recent": {"recent", "latest", "new", "past", "current"},
            "past": {"past", "recent", "latest", "current"},
            "year": {"year", "annual", "recent"},
            "significant": {"significant", "important", "major", "notable", "key"},
        }
        return aliases.get(term, {term})

    def _looks_like_heading_or_title(self, lowered: str) -> bool:
        words = lowered.split()
        if len(words) <= 8 and not re.search(r"[.!?]", lowered):
            return True
        title_like_patterns = [
            r"^[a-z0-9 ,:'\-]+:$",
            r"^[a-z0-9 ,:'\-]+\?$",
            r"^(overview|background|summary|key points|takeaways|introduction)$",
        ]
        return any(re.match(pattern, lowered) for pattern in title_like_patterns)

    def _synthesize_claim(self, query: str, citations: List[Dict[str, Any]], mode: str) -> str:
        ranked_excerpts = []
        for item in citations:
            excerpt = self._clean_claim_text(item.get("excerpt", ""))
            score = self._claim_relevance_score(excerpt, query, allow_question=False)
            if score <= 0:
                continue
            ranked_excerpts.append((score + float(item.get("relevance_score", 0.0)), excerpt, item))

        if not ranked_excerpts:
            return ""

        ranked_excerpts.sort(key=lambda item: item[0], reverse=True)
        lead_excerpt = self._summarize_text(ranked_excerpts[0][1])
        if self._claim_relevance_score(lead_excerpt, query, allow_question=False) <= 0:
            return ""

        supporting_excerpts = [item[1] for item in ranked_excerpts[:3]]
        supporting_sources = {item[2].get("source", "") for item in ranked_excerpts[:3] if item[2].get("source")}
        common_terms = self._common_terms(query, " ".join(supporting_excerpts))
        corroboration_note = ""
        if len(supporting_sources) > 1 and common_terms:
            theme = ", ".join(common_terms[:3])
            corroboration_note = f"Corroborated across {len(supporting_sources)} sources, with recurring focus on {theme}."
        elif len(supporting_sources) > 1:
            corroboration_note = f"Corroborated by {len(supporting_sources)} independent sources."

        if mode == "research" and corroboration_note:
            return f"{lead_excerpt} {corroboration_note}"
        if corroboration_note:
            return f"{lead_excerpt} {corroboration_note}"
        return lead_excerpt

    def _build_sources(self, citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        seen = set()
        sources = []
        for item in citations:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "title": item.get("title", "Untitled"),
                    "url": url,
                    "source": item.get("source", ""),
                }
            )
        return sources

    def _citation_relevance_by_id(self, citations: List[Dict[str, Any]], citation_id: int) -> float:
        for citation in citations:
            if citation.get("id") == citation_id:
                return float(citation.get("relevance_score", 0.0))
        return 0.0

    def _supplemental_finding_for_subquestion(
        self,
        subquestion: str,
        citations: List[Dict[str, Any]],
        assigned_ids: set[int],
    ) -> Dict[str, Any] | None:
        ranked = []
        for citation in citations:
            excerpt = self._clean_claim_text(citation.get("excerpt", ""))
            score = self._claim_relevance_score(excerpt, subquestion, allow_question=False)
            if score <= 0:
                continue
            ranked.append((score + float(citation.get("relevance_score", 0.0)), citation, excerpt))

        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return None

        top = ranked[0][1]
        supporting_ids = [top.get("id")]
        for _, citation, _ in ranked[1:4]:
            cid = citation.get("id")
            if cid and cid not in supporting_ids and cid not in assigned_ids:
                supporting_ids.append(cid)
            if len(supporting_ids) >= 2:
                break

        claim = self._summarize_text(ranked[0][2])
        if not claim:
            return None
        return {"claim": claim, "citation_ids": [cid for cid in supporting_ids if cid]}

    def _findings_by_subquestion(self, query: str, findings: List[Dict[str, Any]]) -> List[tuple[str, List[Dict[str, Any]]]]:
        subquestions = self.planner.decompose_query(query)
        if len(subquestions) <= 1 or not findings:
            return []

        grouped: List[tuple[str, List[Dict[str, Any]]]] = []
        used_indexes: set[int] = set()
        for subquestion in subquestions:
            matches = [
                (idx, finding)
                for idx, finding in enumerate(findings)
                if idx not in used_indexes and self._claim_relevance_score(finding.get("claim", ""), subquestion, allow_question=False) > 0
            ]
            if not matches:
                continue
            grouped.append((subquestion, [finding for idx, finding in matches[:2]]))
            used_indexes.update(idx for idx, _ in matches[:2])

        remaining = [finding for idx, finding in enumerate(findings) if idx not in used_indexes]
        if remaining:
            label = "Additional grounded evidence"
            if grouped:
                grouped.append((label, remaining[:2]))
            else:
                grouped.append((subquestions[0], remaining[:2]))
        return grouped

    def _build_summary(self, query: str, findings: List[Dict[str, Any]], citations: List[Dict[str, Any]], mode: str) -> str:
        if not findings:
            return "No grounded synthesis was produced from the fetched sources."

        support = f"Supported by {len(citations)} citations across {len(self._build_sources(citations))} sources."
        grouped = self._findings_by_subquestion(query, findings)
        if mode == "research" and grouped:
            sections = []
            for subquestion, items in grouped[:3]:
                label = subquestion.rstrip("?")
                lead = items[0]["claim"]
                sections.append(f"{label}: {lead}")
            return "\n\n".join(sections + [support])

        lead = findings[0]["claim"]
        if mode == "research" and len(findings) > 1:
            return f"{lead} The research pass also surfaced {len(findings) - 1} additional grounded finding(s). {support}"
        if mode == "deep" and len(findings) > 1:
            return f"{lead} Additional evidence highlights tradeoffs and corroborating context. {support}"
        return f"{lead} {support}"

    def _build_direct_answer(self, query: str, findings: List[Dict[str, Any]], summary: str, mode: str) -> str:
        if not findings:
            return summary

        grouped = self._findings_by_subquestion(query, findings)
        if mode == "research" and grouped:
            sections = []
            for subquestion, items in grouped[:3]:
                label = subquestion.rstrip("?")
                details = [item["claim"] for item in items[:2]]
                body = " ".join(details)
                sections.append(f"{label}: {body}")
            answer = "\n\n".join(sections)
            if len(answer) < 500 and len(findings) > len(grouped):
                extras = " ".join(item["claim"] for item in findings[len(grouped):len(grouped) + 2])
                if extras:
                    answer = f"{answer}\n\nAdditional context: {extras}"
            return answer

        if mode == "deep" and len(findings) > 1:
            lead = findings[0]["claim"]
            support_points = "; ".join(item["claim"] for item in findings[1:])
            return f"{lead} The evidence also indicates: {support_points}."
        if mode == "research" and len(findings) > 1:
            answer = findings[0]["claim"]
            return f"{answer} Key supporting points: {'; '.join(item['claim'] for item in findings[1:3])}."
        return findings[0]["claim"]

    def _mode_profile(self, mode: str) -> Dict[str, str]:
        if mode == "research":
            return {"answer_style": "multi-finding synthesis", "search_behavior": "iterative follow-up collection"}
        if mode == "deep":
            return {"answer_style": "focused synthesis", "search_behavior": "single-pass high-quality collection"}
        return {"answer_style": "concise synthesis", "search_behavior": "single-pass collection"}

    def _summarize_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip(" ;,-")
        if not cleaned:
            return ""
        parts = re.split(r"(?<=[.!?])\s+|\s[;:-]\s", cleaned)
        sentence = parts[0].strip() if parts else cleaned
        return sentence[:280].rstrip(" ,;:-")

    def _common_terms(self, query: str, text: str) -> List[str]:
        query_terms = self._query_terms(query)
        blocked = query_terms | {"study", "studies", "research", "article", "journal", "report", "reports", "data", "results", "using"}
        counts: Counter[str] = Counter()
        for term in re.findall(r"[a-z0-9]+", text.lower()):
            if len(term) < 4 or term in blocked:
                continue
            counts[term] += 1
        return [term for term, count in counts.most_common(5) if count > 1]

    def _confidence(self, pages: List[Dict[str, Any]], errors: List[str]) -> str:
        if not pages:
            return "low"
        avg_quality = sum(p.get("quality_score", 0.0) for p in pages) / max(1, len(pages))
        if avg_quality >= 0.5 and len(errors) <= 1:
            return "high"
        if avg_quality >= 0.25:
            return "medium"
        return "low"

    async def _merge_vane_synthesis(
        self,
        query: str,
        deep_synthesis: Dict[str, Any],
        direct_answer: str,
        summary: str,
        body: str,
        mode: str,
    ) -> tuple[str, str, str, Dict[str, Any]]:
        vane_answer = self._normalize_vane_text(deep_synthesis.get("answer", ""))
        vane_summary = self._normalize_vane_text(deep_synthesis.get("summary") or deep_synthesis.get("message") or "")
        accepted, acceptance_reason, rejection_reason = self._assess_vane_answer(vane_answer if vane_answer else vane_summary)

        decision = {
            "used": True,
            "body_preserved": accepted,
            "accepted": accepted,
            "acceptance_reason": acceptance_reason,
            "rejection_reason": rejection_reason,
            "body_source": "vane_answer" if accepted else "local_synthesis",
            "direct_answer_source": "local_synthesis",
            "summary_source": "local_synthesis",
        }

        if accepted:
            preserved = vane_answer or vane_summary
            body = preserved
            shaped = await self._shape_vane_summaries(query=query, body=preserved, vane_summary=vane_summary)
            if shaped:
                direct_answer = shaped["direct_answer"]
                summary = shaped["summary"]
                decision["direct_answer_source"] = shaped["direct_answer_source"]
                decision["summary_source"] = shaped["summary_source"]
            else:
                direct_answer = self._condense_vane_text(preserved, max_len=1800, max_sentences=6, preserve_structure=True)
                decision["direct_answer_source"] = "fallback_truncation"
                if vane_summary:
                    summary = self._condense_vane_text(vane_summary, max_len=900, max_sentences=3, preserve_structure=True)
                    decision["summary_source"] = "fallback_vane_summary"
                else:
                    summary = self._condense_vane_text(direct_answer, max_len=900, max_sentences=3, preserve_structure=True)
                    decision["summary_source"] = "fallback_truncation"
        elif vane_summary:
            summary = self._condense_vane_text(vane_summary, max_len=900, max_sentences=3, preserve_structure=True)
            decision["summary_source"] = "fallback_vane_summary"

        return body, direct_answer, summary, decision

    async def _shape_vane_summaries(self, query: str, body: str, vane_summary: str) -> Dict[str, str] | None:
        llm_shaped = await self.compiler.summarize_vane_content(query=query, body=body, vane_summary=vane_summary or None)
        if llm_shaped:
            direct_answer = self._normalize_vane_text(llm_shaped.get("direct_answer", ""))
            summary = self._normalize_vane_text(llm_shaped.get("summary", ""))
            if direct_answer and summary and not self._is_truncation_like(body, direct_answer) and not self._is_truncation_like(direct_answer, summary):
                return {
                    "direct_answer": direct_answer,
                    "summary": summary,
                    "direct_answer_source": llm_shaped.get("direct_source", "llm_summary"),
                    "summary_source": llm_shaped.get("summary_source", "llm_summary"),
                }

        direct_answer = ""
        direct_source = ""
        if vane_summary and self._looks_like_good_vane_summary(vane_summary, body):
            direct_answer = self._normalize_vane_text(vane_summary)
            direct_source = "vane_summary"
        elif vane_summary and not self._is_truncation_like(body, vane_summary):
            direct_answer = self._normalize_vane_text(vane_summary)
            direct_source = "vane_summary"

        if not direct_answer:
            return None

        summary = self._compress_summary_text(direct_answer, max_sentences=2, max_len=320)
        if not summary or self._is_truncation_like(direct_answer, summary):
            return None

        return {
            "direct_answer": direct_answer,
            "summary": summary,
            "direct_answer_source": direct_source,
            "summary_source": "llm_summary" if direct_source != "vane_summary" else "vane_summary",
        }

    def _normalize_vane_text(self, text: str) -> str:
        cleaned = str(text or "").replace("\r\n", "\n").strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    def _assess_vane_answer(self, text: str) -> tuple[bool, str, str]:
        normalized = self._normalize_vane_text(text)
        if not normalized:
            return False, "", "empty"

        lowered = re.sub(r"\s+", " ", normalized.lower()).strip()
        filler_phrases = [
            "no results found",
            "i was not able to find the information",
            "i couldn't find enough information",
            "there is insufficient information available",
            "i do not have enough information",
            "unable to find relevant information",
            "sorry, but i couldn't find",
        ]
        if any(phrase in lowered for phrase in filler_phrases):
            return False, "", "filler_phrase"

        stripped = re.sub(r"[^a-z0-9]", "", lowered)
        if len(stripped) < 120:
            return False, "", "too_short"

        sentence_count = len([s for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()])
        paragraph_count = len([p for p in re.split(r"\n\s*\n", normalized) if p.strip()])
        if sentence_count < 3 and paragraph_count < 2:
            return False, "", "insufficient_structure"

        if not re.search(r"\b(is|are|was|were|because|through|however|while|according|suggests|shows|reported|observed|measured)\b", lowered):
            return False, "", "low_information_density"

        return True, "substantive_longform", ""

    def _looks_like_good_vane_summary(self, summary: str, body: str) -> bool:
        normalized_summary = self._normalize_vane_text(summary)
        if not normalized_summary:
            return False
        if len(normalized_summary) < 40:
            return False
        if self._is_truncation_like(body, normalized_summary):
            return False
        return bool(re.search(r"\b(is|are|was|were|because|through|while|can|may|shows|suggests|found)\b", normalized_summary.lower()))

    def _is_truncation_like(self, source: str, candidate: str) -> bool:
        normalized_source = re.sub(r"\s+", " ", self._normalize_vane_text(source)).strip().lower()
        normalized_candidate = re.sub(r"\s+", " ", self._normalize_vane_text(candidate)).strip().lower()
        if not normalized_source or not normalized_candidate:
            return False
        if normalized_source.startswith(normalized_candidate):
            return True
        if normalized_candidate in normalized_source[: max(len(normalized_candidate) + 80, int(len(normalized_source) * 0.7))]:
            return True
        return False

    def _compress_summary_text(self, text: str, max_sentences: int, max_len: int) -> str:
        cleaned = self._normalize_vane_text(text)
        if not cleaned:
            return ""

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
        if not sentences:
            return cleaned[:max_len].rstrip(" ,;:-")

        picked: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            lowered = sentence.lower()
            if any(marker in lowered for marker in ("for example", "according to", "such as", "including", "recent observations")):
                continue
            next_text = " ".join(picked + [sentence]).strip()
            if len(next_text) > max_len:
                break
            picked.append(sentence)
            if len(picked) >= max_sentences:
                break

        if not picked:
            picked = [sentences[0].strip()]

        compressed = " ".join(picked).strip()
        compressed = re.sub(r"\b(in particular|notably|overall),?\s+", "", compressed, flags=re.IGNORECASE)
        return compressed[:max_len].rstrip(" ,;:-")

    def _condense_vane_text(self, text: str, max_len: int, max_sentences: int, preserve_structure: bool = False) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return ""

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if preserve_structure and len(paragraphs) > 1:
            pieces: list[str] = []
            total_sentences = 0
            for paragraph in paragraphs:
                plain = re.sub(r"\s+", " ", paragraph).strip()
                if not plain:
                    continue
                if len(re.sub(r"^[#*_`>\-\s]+", "", plain).strip()) < 20:
                    continue
                paragraph_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if s.strip()]
                if not paragraph_sentences:
                    candidate = plain[:max_len].rstrip(" ,;:-")
                else:
                    kept: list[str] = []
                    for sentence in paragraph_sentences:
                        if total_sentences + len(kept) >= max_sentences:
                            break
                        next_text = " ".join(kept + [sentence]).strip()
                        if len(next_text) > max_len:
                            break
                        kept.append(sentence)
                    candidate = " ".join(kept).strip() if kept else paragraph_sentences[0].strip()
                joined = "\n\n".join(pieces + [candidate]).strip()
                if len(joined) > max_len:
                    break
                pieces.append(candidate)
                total_sentences += len([s for s in re.split(r"(?<=[.!?])\s+", candidate) if s.strip()])
                if total_sentences >= max_sentences:
                    break
            if pieces:
                return "\n\n".join(pieces).strip()[:max_len].rstrip(" ,;:-")

        candidate = cleaned
        for paragraph in paragraphs:
            plain = re.sub(r"\s+", " ", paragraph).strip()
            markdown_heading = bool(re.match(r"^#{1,6}\s+", plain))
            plain = re.sub(r"^[#*_`>\-\s]+", "", plain).strip()
            if not plain:
                continue
            if markdown_heading:
                if len(plain) < 140 or not re.search(r"[.!?]", plain):
                    continue
            if len(plain) < 40 and re.fullmatch(r"[A-Za-z ]+:?", plain):
                continue
            candidate = plain
            break

        candidate = re.sub(r"^[#*_`>\-\s]+", "", candidate).strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", candidate) if s.strip()]
        if not sentences:
            return candidate[:max_len].rstrip(" ,;:-")

        pieces = []
        for sentence in sentences[:max_sentences]:
            next_text = " ".join(pieces + [sentence]).strip()
            if len(next_text) > max_len:
                break
            pieces.append(sentence)

        condensed = " ".join(pieces).strip() if pieces else sentences[0].strip()
        return condensed[:max_len].rstrip(" ,;:-")

    def _record_run(
        self,
        endpoint: str,
        query: str,
        mode: str,
        success: bool,
        citations_count: int,
        sources_count: int,
        confidence: str | None,
        warnings: List[str],
        errors: List[str],
    ) -> None:
        self.run_history.append(
            RunHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                endpoint=endpoint,
                query=query,
                mode=mode,
                success=success,
                citations_count=citations_count,
                sources_count=sources_count,
                confidence=confidence,
                warnings=warnings[:5],
                errors=errors[:5],
            )
        )

    def _select_vane_depth(self, query: str, requested_depth: str, mode: str) -> str:
        if requested_depth == "quick":
            return "balanced"
        if requested_depth == "quality":
            return "quality"

        if mode == "deep":
            return "balanced"
        if mode == "research":
            profile = self.planner.classify_complexity(query)
            if (
                profile.get("is_comparison")
                or profile.get("is_recommendation")
                or profile.get("is_specific")
                or profile.get("is_long")
            ):
                return "quality"
            return "balanced"

        return "balanced"
