from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from app.cache.memory_cache import InMemoryCache
from app.core.config import AppConfig
from app.models.contracts import PerplexitySearchRequest, PerplexitySearchResponse, PerplexitySearchResult
from app.models.contracts import SearchDiagnostics, SearchRequest, SearchResponse
from app.providers.router import ProviderRouter
from app.services.fetcher import PageFetcher
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker
from app.services.compiler import ResultCompiler
from app.services.vane import VaneClient


logger = logging.getLogger(__name__)


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

    async def execute_search(self, req: SearchRequest) -> SearchResponse:
        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        selected_mode = self.planner.choose_mode(req.mode, req.query)
        mode_budget = self.config.modes[selected_mode]

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

        plan = self.planner.initial_plan(req.query, selected_mode)
        all_result_sets: List[List[Dict[str, Any]]] = []
        provider_trace: List[Dict[str, Any]] = []
        warnings: List[str] = []
        errors: List[str] = []
        followups: List[str] = []
        search_cache_state = {"hits": 0, "misses": 0}

        iterations = 1
        if selected_mode == "research":
            iterations = min(req.max_iterations, mode_budget.max_queries)

        seen_titles: List[str] = []
        for cycle in range(iterations):
            query_text = plan[min(cycle, len(plan) - 1)]["text"] if cycle < len(plan) else req.query
            if cycle > 0 and selected_mode == "research":
                query_text = self.planner.followup_query(req.query, seen_titles)
                plan.append({"text": query_text, "purpose": "followup"})
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
                max_attempts=mode_budget.max_provider_attempts,
                request_id=request_id,
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
        diverse = self.ranker.diversity_filter(fused, mode_budget.max_pages_to_fetch)

        pages = await self._fetch_pages(req.query, diverse)
        citations = self._build_citations(req.query, pages)
        findings = self._build_findings(citations)
        sources = self._build_sources(citations)
        confidence = self._confidence(pages, errors)

        deep_synthesis = None
        if selected_mode in {"deep", "research"}:
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
            if deep_synthesis.get("error"):
                warnings.append(f"Vane unavailable: {deep_synthesis['error']}")

        direct_answer = findings[0]["claim"] if findings else ""
        summary = f"Collected {len(citations)} citations across {len(sources)} sources using mode={selected_mode}."

        runtime = {
            "strict_runtime": req.strict_runtime,
            "provider_count": len(self.router.health_snapshot()),
            "vane_enabled": bool(self.config.vane.enabled),
        }

        diagnostics = SearchDiagnostics(
            warnings=warnings,
            errors=errors,
            runtime=runtime,
            query_plan=plan,
            iterations=iterations,
            coverage_notes=[
                f"fused_results={len(fused)}",
                f"fetched_pages={len(pages)}",
            ],
            search_count=len(plan),
            fetched_count=len(pages),
            ranked_passage_count=len(citations),
            provider_trace=provider_trace,
            cache={
                "search": search_cache_state,
                "page": self.page_cache.stats(),
            },
        )

        payload: Dict[str, Any] = {
            "query": req.query,
            "mode": selected_mode,
            "direct_answer": direct_answer,
            "summary": summary,
            "findings": findings,
            "citations": citations if req.include_citations else [],
            "sources": sources,
            "follow_up_queries": followups,
            "diagnostics": diagnostics.model_dump(),
            "timings": {"total_ms": int((time.perf_counter() - started) * 1000)},
            "confidence": confidence,
        }

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

        return SearchResponse.model_validate(payload)

    async def execute_perplexity_search(self, req: PerplexitySearchRequest) -> PerplexitySearchResponse:
        request_id = req.trace_id or uuid.uuid4().hex[:8]
        queries = self._normalize_queries(req.query)
        if not queries:
            raise ValueError("query is required")

        mode = req.mode or self._infer_compat_mode(req)
        source_mode = self._map_search_mode(req.search_mode)
        results: List[PerplexitySearchResult] = []
        seen_urls: set[str] = set()

        logger.info(
            "event=perplexity_search_start request_id=%s query_count=%s max_results=%s mode=%s search_mode=%s",
            request_id,
            len(queries),
            req.max_results,
            mode,
            req.search_mode or "none",
        )

        for query in queries:
            internal = SearchRequest(
                query=query,
                mode=mode,
                source_mode=source_mode,
                depth=self._select_depth(req),
                max_iterations=self._select_iterations(req),
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
                },
            )
            response = await self.execute_search(internal)
            items = self._perplexity_results_from_response(response)
            items = self._apply_perplexity_filters(items, req)

            compiler_input = []
            for idx, item in enumerate(items, start=1):
                payload = item.model_dump(exclude_none=True)
                payload["candidate_id"] = idx
                compiler_input.append(payload)
            compiled = await self.compiler.compile_perplexity_results(
                query=query,
                candidates=compiler_input,
                max_results=req.max_results,
                max_tokens_per_page=req.max_tokens_per_page,
            )
            if compiled:
                items = [PerplexitySearchResult.model_validate(item) for item in compiled]

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
        return {
            "cache_search": self.search_cache.stats(),
            "cache_page": self.page_cache.stats(),
            "providers": len(self.router.health_snapshot()),
        }

    async def _search_once(
        self,
        query: str,
        mode: str,
        source_mode: str,
        depth: str,
        max_attempts: int,
        request_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
        options = {
            "limit": self.config.modes[mode].max_pages_to_fetch,
            "source_mode": source_mode,
            "depth": depth,
            "request_id": request_id,
        }
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
        if req.max_tokens_per_page and req.max_tokens_per_page >= 4096:
            return "quality"
        if req.max_tokens and req.max_tokens >= 50000:
            return "quality"
        if req.search_domain_filter or req.search_language_filter or req.country:
            return "balanced"
        return "balanced"

    def _select_iterations(self, req: PerplexitySearchRequest) -> int:
        if req.max_tokens and req.max_tokens >= 50000:
            return 4
        if req.max_tokens_per_page and req.max_tokens_per_page >= 4096:
            return 3
        return 2

    def _perplexity_results_from_response(self, response: SearchResponse) -> List[PerplexitySearchResult]:
        citation_map: Dict[str, Dict[str, Any]] = {
            citation.url: citation.model_dump() if hasattr(citation, "model_dump") else dict(citation)
            for citation in response.citations
        }
        results: List[PerplexitySearchResult] = []
        for source in response.sources:
            url = source.url
            citation = citation_map.get(url, {})
            snippet = citation.get("excerpt") or response.summary or response.direct_answer or ""
            results.append(
                PerplexitySearchResult(
                    title=source.title or citation.get("title") or "Untitled",
                    url=url,
                    snippet=snippet,
                    date=citation.get("published_at") or None,
                    last_updated=citation.get("last_updated") or None,
                )
            )
        if not results and response.direct_answer:
            results.append(
                PerplexitySearchResult(
                    title=response.query,
                    url="",
                    snippet=response.direct_answer,
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
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ""
        q_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        best = ""
        best_score = -1
        for line in lines[:60]:
            terms = set(re.findall(r"[a-z0-9]+", line.lower()))
            score = len(q_terms & terms)
            if score > best_score and len(line) >= 40:
                best = line
                best_score = score
        return (best or lines[0])[:320]

    def _build_citations(self, query: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        citations = []
        for idx, page in enumerate(pages[:10], start=1):
            url = page.get("url", "")
            parsed = urlparse(url)
            source = parsed.netloc.lower()
            citations.append(
                {
                    "id": idx,
                    "title": page.get("title", "Untitled"),
                    "url": url,
                    "source": source,
                    "excerpt": self._best_excerpt(page.get("content", ""), query),
                    "published_at": page.get("published_at", ""),
                    "last_updated": page.get("last_updated") or None,
                    "language": page.get("language") or None,
                    "relevance_score": round(float(page.get("quality_score", 0.0)), 3),
                    "passage_id": f"p{idx}-{re.sub(r'[^a-z0-9]+', '-', source)[:40] or 'source'}",
                }
            )
        return citations

    def _build_findings(self, citations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        findings = []
        for item in citations[:6]:
            excerpt = item.get("excerpt", "")
            if excerpt:
                findings.append({"claim": excerpt, "citation_ids": [item["id"]]})
        return findings

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

    def _confidence(self, pages: List[Dict[str, Any]], errors: List[str]) -> str:
        if not pages:
            return "low"
        avg_quality = sum(p.get("quality_score", 0.0) for p in pages) / max(1, len(pages))
        if avg_quality >= 0.5 and len(errors) <= 1:
            return "high"
        if avg_quality >= 0.25:
            return "medium"
        return "low"

    def _select_vane_depth(self, query: str, requested_depth: str, mode: str) -> str:
        if requested_depth == "quick":
            return "quick"
        if requested_depth == "quality":
            return "quality"

        profile = self.planner.classify_complexity(query)
        if mode in {"deep", "research"} and (
            profile.get("is_comparison")
            or profile.get("is_recommendation")
            or profile.get("is_specific")
            or profile.get("is_long")
        ):
            return "quality"

        default_mode = self.config.vane.default_optimization_mode
        return default_mode if default_mode in {"speed", "balanced", "quality"} else "balanced"
