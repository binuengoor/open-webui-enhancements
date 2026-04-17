"""
title: Enhanced Websearch Function
author: GitHub Copilot
version: 1.1.0
license: MIT
description: >
    Function/pipe-oriented unified Open-WebUI web research workflow that
    combines SearXNG retrieval, query expansion, reciprocal rank fusion,
    FlareSolverr-backed scraping, optional Vane deep synthesis, and iterative
    research orchestration using Open-WebUI model calls.
requirements: beautifulsoup4, requests
"""

import asyncio
import concurrent.futures
import json
import logging
import re
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from open_webui.models.users import Users
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.misc import get_last_user_message, pop_system_message


logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class EventEmitter:
    def __init__(self, event_emitter: Optional[Callable[[dict], Any]]):
        self.event_emitter = event_emitter

    async def status(self, description: str, status: str = "in_progress", done: bool = False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": status,
                        "description": description,
                        "done": done,
                    },
                }
            )

    async def message(self, content: str):
        if self.event_emitter:
            await self.event_emitter({"type": "message", "data": {"content": content}})

    async def citation(self, title: str, url: str, content: str):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "citation",
                    "data": {
                        "document": [content[:1200]],
                        "metadata": [{"source": url, "title": title}],
                        "source": {"name": title, "url": url},
                    },
                }
            )


class PageScraper:
    CAPTCHA_INDICATORS = [
        "captcha",
        "cf-challenge",
        "challenge-platform",
        "just a moment",
        "checking your browser",
        "attention required",
        "access denied",
        "security check",
        "ddos protection",
    ]

    def __init__(self, valves):
        self.valves = valves
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.valves.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )

    def _looks_blocked(self, response: requests.Response) -> bool:
        if response.status_code in (403, 429, 503):
            return True
        body = response.text[:5000].lower()
        hits = sum(1 for marker in self.CAPTCHA_INDICATORS if marker in body)
        return hits >= 2 or (response.status_code == 200 and len(response.text) < 2000 and hits >= 1)

    def _is_pdf(self, url: str, response: Optional[requests.Response] = None) -> bool:
        if url.lower().endswith(".pdf"):
            return True
        if response and "application/pdf" in response.headers.get("Content-Type", "").lower():
            return True
        return False

    def _extract_pdf_text(self, raw: bytes) -> str:
        try:
            import pypdf

            reader = pypdf.PdfReader(BytesIO(raw))
            parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            if parts:
                return "\n\n".join(parts)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("pypdf extraction failed: %s", exc)

        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(BytesIO(raw))
            parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            if parts:
                return "\n\n".join(parts)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("PyPDF2 extraction failed: %s", exc)

        return "[PDF detected but no PDF extraction backend is available]"

    def _fetch_via_flaresolverr(self, url: str) -> Optional[str]:
        if not self.valves.FLARESOLVERR_URL:
            return None
        try:
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": self.valves.FLARESOLVERR_TIMEOUT * 1000,
            }
            resp = requests.post(
                self.valves.FLARESOLVERR_URL,
                json=payload,
                timeout=self.valves.FLARESOLVERR_TIMEOUT + 10,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") == "ok":
                return body.get("solution", {}).get("response", "")
        except Exception as exc:
            logger.warning("FlareSolverr failed for %s: %s", url, exc)
        return None

    def _extract_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()

        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find("div", {"role": "main"})
            or soup.body
            or soup
        )
        text = main.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        normalized = "\n".join(lines)

        if len(normalized) > self.valves.MAX_PAGE_CONTENT_CHARS:
            return normalized[: self.valves.MAX_PAGE_CONTENT_CHARS] + "\n\n[Content truncated]"
        return normalized

    def scrape(self, url: str) -> Dict[str, Any]:
        result = {
            "url": url,
            "title": "",
            "content": "",
            "source": "direct",
            "error": None,
        }
        if not urlparse(url).scheme:
            url = "https://" + url
            result["url"] = url

        try:
            resp = self.session.get(url, timeout=self.valves.REQUEST_TIMEOUT, allow_redirects=True)
            if self._is_pdf(url, resp):
                result["title"] = url.split("/")[-1]
                result["content"] = self._extract_pdf_text(resp.content)
                result["source"] = "pdf"
                return result

            html = resp.text
            if self._looks_blocked(resp):
                fallback = self._fetch_via_flaresolverr(url)
                if fallback:
                    html = fallback
                    result["source"] = "flaresolverr"
                else:
                    result["error"] = "Blocked by anti-bot and fallback failed"

            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            result["title"] = title_tag.get_text(strip=True) if title_tag else urlparse(url).netloc
            result["content"] = self._extract_text(html)

        except requests.RequestException as exc:
            result["error"] = str(exc)

        return result


class Pipe:
    class Valves(BaseModel):
        SEARXNG_BASE_URL: str = Field(default="http://searxng:8080", description="SearXNG base URL")
        VANE_URL: str = Field(default="http://vane:3000", description="Vane base URL")
        FLARESOLVERR_URL: str = Field(default="http://flaresolverr:8191/v1", description="FlareSolverr endpoint; set empty to disable")
        SEARCH_RESULTS_PER_QUERY: int = Field(default=8, ge=3, le=20)
        PAGES_TO_SCRAPE: int = Field(default=5, ge=1, le=12)
        CONCURRENT_SCRAPE_WORKERS: int = Field(default=4, ge=1, le=12)
        ENABLE_VANE_DEEP: bool = Field(default=True, description="Allow deep synthesis via Vane")
        VANE_CHAT_MODEL_PROVIDER_ID: str = Field(default="", description="Vane chat provider ID")
        VANE_EMBEDDING_MODEL_PROVIDER_ID: str = Field(default="", description="Vane embedding provider ID")
        RESEARCH_MODEL: str = Field(default="", description="Open-WebUI configured model key used for research planning and synthesis")

        INTERNAL_DEFAULTS: ClassVar[Dict[str, Any]] = {
            "REQUEST_TIMEOUT": 15,
            "FLARESOLVERR_TIMEOUT": 60,
            "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "QUERY_VARIANTS_LIMIT": 4,
            "RRF_K": 60,
            "SEARCH_CATEGORIES": "general",
            "SEARCH_ENGINES": "",
            "SEARCH_LANGUAGE": "en",
            "SEARCH_TIME_RANGE": "",
            "MAX_PAGE_CONTENT_CHARS": 25000,
            "MIN_CONTENT_CHARS": 80,
            "INJECT_DATETIME": True,
            "DATETIME_FORMAT": "%Y-%m-%d %A %B %d",
            "TIMEZONE": "UTC",
            "VANE_CHAT_MODEL_KEY": "auto-main",
            "VANE_EMBEDDING_MODEL_KEY": "openrouter/perplexity/pplx-embed-v1-0.6b",
            "VANE_TIMEOUT": 45,
            "RESEARCH_MODEL_TEMPERATURE": 0.3,
            "RESEARCH_MODEL_MAX_TOKENS": 4096,
            "RESEARCH_MIN_ITERATIONS": 2,
            "RESEARCH_MAX_CONTEXT_SOURCES": 20,
            "IGNORED_DOMAINS": "",
        }

        def __getattr__(self, name: str) -> Any:
            if name in self.INTERNAL_DEFAULTS:
                return self.INTERNAL_DEFAULTS[name]
            raise AttributeError(name)

    class UserValves(BaseModel):
        mode: str = Field(default="auto", description="auto, fast, deep, research")
        show_status_updates: bool = Field(default=True)
        include_citations: bool = Field(default=True)
        show_reasoning: bool = Field(default=True)
        max_iterations: int = Field(default=5, ge=1, le=10, description="Max research cycles in research mode")

    def __init__(self):
        self.valves = self.Valves()

    def _cfg(self, key: str, user_valves: Optional[Any], default: Any = None) -> Any:
        if user_valves and hasattr(user_valves, key):
            value = getattr(user_valves, key)
            if value is not None:
                return value
        return getattr(self.valves, key, default)

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            parsed = urlparse("https://" + url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        clean_query = {k: v for k, v in query.items() if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}}
        return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), parsed.path or "/", "", urlencode(clean_query, doseq=True), ""))

    def _ignored_domains(self) -> set:
        if not self.valves.IGNORED_DOMAINS.strip():
            return set()
        return {d.strip().lower() for d in self.valves.IGNORED_DOMAINS.split(",") if d.strip()}

    def _term_signature(self, text: str) -> set:
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "what",
            "when",
            "where",
            "which",
            "who",
            "whom",
            "why",
            "how",
            "are",
            "was",
            "were",
            "will",
            "can",
            "could",
            "should",
            "would",
            "does",
            "did",
            "have",
            "has",
            "had",
            "about",
            "into",
            "over",
            "under",
            "than",
            "then",
        }
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 3 and token not in stopwords}

    def _evidence_coverage(self, original_query: str, pages: List[Dict[str, Any]]) -> float:
        query_terms = self._term_signature(original_query)
        if not query_terms or not pages:
            return 0.0
        recent_text = " ".join(f"{page.get('title', '')} {page.get('content', '')[:1000]}" for page in pages[-6:])
        content_terms = self._term_signature(recent_text)
        if not content_terms:
            return 0.0
        return len(query_terms & content_terms) / max(1, len(query_terms))

    def _evidence_redundancy(self, pages: List[Dict[str, Any]]) -> float:
        if len(pages) < 2:
            return 0.0
        signatures = [self._term_signature(f"{page.get('title', '')} {page.get('content', '')[:1200]}") for page in pages[-5:]]
        signatures = [sig for sig in signatures if sig]
        if len(signatures) < 2:
            return 0.0
        overlaps = []
        for left, right in zip(signatures, signatures[1:]):
            union = left | right
            if union:
                overlaps.append(len(left & right) / len(union))
        return sum(overlaps) / len(overlaps) if overlaps else 0.0

    def _missing_query_terms(self, original_query: str, pages: List[Dict[str, Any]]) -> List[str]:
        query_terms = self._term_signature(original_query)
        if not query_terms:
            return []
        page_terms = self._term_signature(" ".join(f"{page.get('title', '')} {page.get('content', '')[:1000]}" for page in pages[-6:]))
        missing = sorted(query_terms - page_terms)
        return missing[:5]

    def _resolve_research_model(self, body: dict) -> Optional[str]:
        configured = self._cfg("RESEARCH_MODEL", None)
        if configured:
            return configured
        model = body.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        if isinstance(model, dict):
            for key in ("id", "name", "model", "value"):
                value = model.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _apply_mode_prefix(self, query: str, requested_mode: str) -> Tuple[str, str, Optional[str]]:
        match = re.match(r"^\s*(fast|deep)\s*:\s*(.*)$", query, flags=re.IGNORECASE)
        if not match:
            return query, requested_mode, None

        forced_mode = match.group(1).lower()
        stripped_query = match.group(2).strip()
        if stripped_query:
            return stripped_query, forced_mode, forced_mode
        return query, forced_mode, forced_mode

    def _is_temporal_query(self, query: str) -> bool:
        temporal = [r"\btoday\b", r"\btomorrow\b", r"\byesterday\b", r"\bcurrent\b", r"\blatest\b", r"\brecent\b", r"\bnews\b", r"\bweather\b", r"\bthis\s+(week|month|year)\b", r"\bnext\s+(week|month|year)\b"]
        return any(re.search(p, query.lower()) for p in temporal)

    def _inject_temporal_context(self, query: str) -> Tuple[str, Dict[str, str]]:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(self.valves.TIMEZONE))
        except Exception:
            now = datetime.utcnow()
        info = {"date": now.strftime("%Y-%m-%d"), "day_name": now.strftime("%A"), "month_name": now.strftime("%B"), "year": now.strftime("%Y"), "time": now.strftime("%H:%M:%S"), "timezone": self.valves.TIMEZONE, "formatted": now.strftime(self.valves.DATETIME_FORMAT)}
        if not self.valves.INJECT_DATETIME or not self._is_temporal_query(query):
            return query, info
        enriched = f"{info['date']} ({info['day_name']}): {query}"
        ql = query.lower()
        if "tomorrow" in ql:
            tmr = now + timedelta(days=1)
            enriched = f"{info['date']} (today) -> {tmr.strftime('%Y-%m-%d')} {tmr.strftime('%A')} (tomorrow): {query}"
        elif "yesterday" in ql:
            yst = now - timedelta(days=1)
            enriched = f"{info['date']} (today) -> {yst.strftime('%Y-%m-%d')} {yst.strftime('%A')} (yesterday): {query}"
        return enriched, info

    def _expand_queries(self, query: str) -> List[str]:
        variants = [query]
        q = query.strip()
        if len(q.split()) > 3:
            variants.extend([f"{q} overview", f"{q} official documentation"])
        if re.search(r"\b(compare|vs|versus|difference|best|alternatives?)\b", q.lower()):
            variants.extend([f"{q} benchmark", f"{q} pros cons"])
        if self._is_temporal_query(q):
            variants.append(f"{q} latest updates")
        deduped, seen = [], set()
        for item in variants:
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(item.strip())
        return deduped[: self.valves.QUERY_VARIANTS_LIMIT]

    def _is_complex_query(self, query: str) -> bool:
        if len(query.split()) > 15:
            return True
        patterns = [r"\bcompare\b", r"\bvs\b", r"\bdeep\b", r"\bresearch\b", r"\btrade[- ]?offs\b", r"\bpros\b", r"\bcons\b", r"\barchitecture\b", r"\bdesign\b", r"\bhow\s+(to|does|can|should|could)\b", r"\bwhy\s+(does|do|is|are)\b"]
        return any(re.search(p, query.lower()) for p in patterns)

    def _source_quality_score(self, url: str, title: str, text: str) -> float:
        score = 0.0
        domain = urlparse(url).netloc.lower()
        if any(domain.endswith(tld) for tld in [".edu", ".gov", ".org"]):
            score += 0.25
        if any(token in domain for token in ["github.com", "arxiv.org", "wikipedia.org", "docs", "developer"]):
            score += 0.2
        if len(text) > 1500:
            score += 0.25
        if len(text) > 5000:
            score += 0.1
        if re.search(r"\bupdated\b|\bpublished\b|\b\d{4}\b", (title + " " + text)[:1200].lower()):
            score += 0.1
        if re.search(r"\bsubscribe\b|\bsign in\b|\bcookie\b", text[:800].lower()):
            score -= 0.1
        return max(0.0, min(1.0, score))

    def _search_searxng(self, query: str) -> List[Dict[str, Any]]:
        params = {"q": query, "format": "json", "number_of_results": self.valves.SEARCH_RESULTS_PER_QUERY, "categories": self.valves.SEARCH_CATEGORIES, "language": self.valves.SEARCH_LANGUAGE}
        if self.valves.SEARCH_TIME_RANGE:
            params["time_range"] = self.valves.SEARCH_TIME_RANGE
        if self.valves.SEARCH_ENGINES:
            params["engines"] = self.valves.SEARCH_ENGINES
        resp = requests.get(f"{self.valves.SEARXNG_BASE_URL.rstrip('/')}/search", params=params, timeout=self.valves.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _rrf_fuse(self, result_sets: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        scores: Dict[str, float] = {}
        merged: Dict[str, Dict[str, Any]] = {}
        ignored = self._ignored_domains()
        for result_set in result_sets:
            for rank, item in enumerate(result_set, start=1):
                raw_url = item.get("url", "")
                if not raw_url:
                    continue
                normalized = self._normalize_url(raw_url)
                domain = urlparse(normalized).netloc.lower()
                if any(ig in domain for ig in ignored):
                    continue
                scores[normalized] = scores.get(normalized, 0.0) + (1.0 / (self.valves.RRF_K + rank))
                if normalized not in merged:
                    merged[normalized] = {"url": normalized, "title": item.get("title", "Untitled"), "snippet": item.get("content", ""), "engines": item.get("engines", [])}
        fused = []
        for url, score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True):
            doc = merged[url]
            doc["rrf_score"] = round(score, 6)
            fused.append(doc)
        return fused

    async def _vane_deep_search(self, query: str, source_mode: str, depth: str) -> Dict[str, Any]:
        if not self.valves.ENABLE_VANE_DEEP:
            return {"enabled": False, "error": "Vane deep search is disabled"}
        if not self.valves.VANE_CHAT_MODEL_PROVIDER_ID or not self.valves.VANE_EMBEDDING_MODEL_PROVIDER_ID:
            return {"enabled": False, "error": "Vane provider IDs are not configured"}
        source_map = {"web": ["web"], "academia": ["academic"], "social": ["discussions"], "all": ["web", "academic", "discussions"]}
        optimization_map = {"quick": "speed", "speed": "speed", "balanced": "balanced", "quality": "quality"}
        payload = {"query": query, "sources": source_map.get(source_mode, ["web"]), "optimizationMode": optimization_map.get(depth, "balanced"), "stream": False, "chatModel": {"providerId": self.valves.VANE_CHAT_MODEL_PROVIDER_ID, "key": self.valves.VANE_CHAT_MODEL_KEY}, "embeddingModel": {"providerId": self.valves.VANE_EMBEDDING_MODEL_PROVIDER_ID, "key": self.valves.VANE_EMBEDDING_MODEL_KEY}}
        try:
            resp = requests.post(f"{self.valves.VANE_URL.rstrip('/')}/api/search", json=payload, timeout=self.valves.VANE_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            sources = []
            for src in data.get("sources", []):
                meta = src.get("metadata", {})
                sources.append({"title": meta.get("title", "Untitled"), "url": meta.get("url", ""), "content": src.get("content", "")})
            return {"enabled": True, "message": data.get("message", ""), "sources": sources}
        except Exception as exc:
            return {"enabled": True, "error": str(exc), "sources": []}

    async def _llm_call(self, request: Any, user: Any, messages: List[Dict[str, str]], model: Optional[str] = None, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        model = model or self.valves.RESEARCH_MODEL or "default"
        payload = {"model": model, "messages": messages, "stream": False, "temperature": temperature, "max_tokens": max_tokens}
        response = await generate_chat_completion(request, payload, user=user)
        if isinstance(response, dict):
            return response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if hasattr(response, "body_iterator"):
            full = ""
            async for chunk in response.body_iterator:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8")
                for line in chunk.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        if line == "data: [DONE]":
                            continue
                        try:
                            data = json.loads(line[6:])
                            full += data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        except json.JSONDecodeError:
                            pass
            return full.strip()
        return str(response).strip()

    def _heuristic_followup_query(self, original_query: str, pages: List[Dict[str, Any]]) -> str:
        if not pages:
            return f"{original_query} latest developments"
        missing_terms = self._missing_query_terms(original_query, pages)
        if missing_terms:
            return f"{original_query} {' '.join(missing_terms[:3])}"
        titles = " ".join(page.get("title", "") for page in pages[-5:]).lower()
        if "overview" not in titles:
            return f"{original_query} implementation details"
        return f"{original_query} limitations tradeoffs"

    async def _next_research_query(self, original_query: str, enriched_query: str, pages: List[Dict[str, Any]], iterations_used: int, request: Any, user: Any, model_name: Optional[str]) -> str:
        if iterations_used == 1:
            return enriched_query
        if model_name:
            summary = "\n".join(f"- {p.get('title', 'Untitled')}: {p.get('content', '')[:220]}" for p in pages[-6:])
            prompt = (
                f"Original query: {original_query}\n\n"
                f"Collected findings:\n{summary[:3500]}\n\n"
                "Generate ONE focused follow-up web search query that fills the most critical gap. "
                "Return only the query text."
            )
            candidate = await self._llm_call(request, user, [{"role": "user", "content": prompt}], model=model_name, temperature=self.valves.RESEARCH_MODEL_TEMPERATURE, max_tokens=120)
            if candidate:
                return candidate.strip().strip('"')
        return self._heuristic_followup_query(original_query, pages)

    async def _should_continue_research(self, original_query: str, pages: List[Dict[str, Any]], cycle: int, max_iterations: int, request: Any, user: Any, model_name: Optional[str]) -> bool:
        if cycle < self.valves.RESEARCH_MIN_ITERATIONS:
            return True
        coverage = self._evidence_coverage(original_query, pages)
        redundancy = self._evidence_redundancy(pages)
        if coverage >= 0.65 and redundancy >= 0.65:
            return False
        if coverage >= 0.8:
            return redundancy < 0.8 and len(pages) < max_iterations * 2

        if model_name:
            prompt = (
                f"Original query: {original_query}\n"
                f"Cycle: {cycle}/{max_iterations}\n"
                f"Pages gathered: {len(pages)}\n"
                f"Coverage: {round(coverage, 3)}\n"
                f"Redundancy: {round(redundancy, 3)}\n"
                f"Recent titles: {[p.get('title', '') for p in pages[-5:]]}\n\n"
                "Reply with only CONTINUE or STOP."
            )
            decision = await self._llm_call(request, user, [{"role": "user", "content": prompt}], model=model_name, temperature=0.1, max_tokens=40)
            if decision:
                return decision.upper().startswith("CONTINUE")
        return len(pages) < max(6, cycle * 2) or coverage < 0.5 or redundancy < 0.55

    def _build_research_context(self, pages: List[Dict[str, Any]]) -> str:
        cap = self.valves.RESEARCH_MAX_CONTEXT_SOURCES
        chunks = []
        for page in pages[:cap]:
            chunks.append(f"=== SOURCE: {page.get('title', 'Untitled')} ({page.get('url', '')}) ===\n{page.get('content', '')[:2000]}")
        return "\n\n".join(chunks)

    async def _search_and_scrape(self, query: str, emitter: EventEmitter) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
        variants = self._expand_queries(query)
        result_sets: List[List[Dict[str, Any]]] = []
        failures: List[str] = []
        await emitter.status(f"Running {len(variants)} query variants via SearXNG")
        for variant in variants:
            try:
                result_sets.append(self._search_searxng(variant))
            except Exception as exc:
                failures.append(f"{variant}: {exc}")
        fused = self._rrf_fuse(result_sets)
        await emitter.status(f"Fused {sum(len(rs) for rs in result_sets)} raw results -> {len(fused)} unique URLs")
        scraper = PageScraper(self.valves)
        scraped: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.valves.CONCURRENT_SCRAPE_WORKERS) as executor:
            futures = {executor.submit(scraper.scrape, item["url"]): item for item in fused[: self.valves.PAGES_TO_SCRAPE]}
            for future in concurrent.futures.as_completed(futures):
                base = futures[future]
                try:
                    page = future.result()
                    text = page.get("content", "")
                    if text and len(text) < self.valves.MIN_CONTENT_CHARS:
                        text = ""
                    score = self._source_quality_score(page.get("url", base["url"]), page.get("title", base["title"]), text)
                    scraped.append({"url": page.get("url", base["url"]), "title": page.get("title", base["title"]), "snippet": base.get("snippet", ""), "content": text or base.get("snippet", ""), "source": page.get("source", "direct"), "error": page.get("error"), "rrf_score": base.get("rrf_score", 0.0), "quality_score": score})
                except Exception as exc:
                    scraped.append({"url": base.get("url", ""), "title": base.get("title", ""), "snippet": base.get("snippet", ""), "content": base.get("snippet", ""), "source": "failed", "error": str(exc), "rrf_score": base.get("rrf_score", 0.0), "quality_score": 0.0})
        scraped = sorted(scraped, key=lambda x: (x.get("quality_score", 0.0), x.get("rrf_score", 0.0)), reverse=True)
        return fused, scraped, failures, variants

    async def _vane_or_fast(self, query: str, source_mode: str, depth: str, emitter: EventEmitter) -> Tuple[str, Optional[Dict[str, Any]]]:
        deep = await self._vane_deep_search(query, source_mode, depth)
        if deep.get("error"):
            await emitter.status("Vane deep synthesis failed; using fast evidence fallback")
        return ("fast_fallback" if deep.get("error") else "deep", deep)

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __event_call__: Optional[Callable[[dict], Awaitable[Any]]] = None,
        __request__: Optional[Any] = None,
        __task__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        user_obj = Users.get_user_by_id(__user__["id"]) if __user__ else None
        all_messages = body.get("messages", [])
        _, messages = pop_system_message(all_messages)
        user_query = get_last_user_message(messages)
        if not user_query:
            return "Please provide a research topic or question."

        user_valves = __user__.get("valves") if __user__ and __user__.get("valves") else None
        requested_mode = self._cfg("mode", user_valves, "auto")
        show_status = self._cfg("show_status_updates", user_valves, True)
        include_citations = self._cfg("include_citations", user_valves, True)
        show_reasoning = self._cfg("show_reasoning", user_valves, True)
        max_iterations = self._cfg("max_iterations", user_valves, 5)
        research_model = self._resolve_research_model(body)
        user_query, requested_mode, mode_prefix_override = self._apply_mode_prefix(
            user_query, requested_mode
        )

        enriched_query, dt_info = self._inject_temporal_context(user_query)
        if show_status:
            await emitter.status(f"Starting unified research: {user_query}")

        # Research mode only for the function version; this is the main reason to use a pipe.
        if requested_mode == "research":
            all_pages: List[Dict[str, Any]] = []
            all_ranked: List[Dict[str, Any]] = []
            seen_urls = set()
            seen_ranked = set()
            queries_used: List[str] = []
            search_failures: List[str] = []

            cycle = 0
            while cycle < max_iterations:
                cycle += 1
                cycle_query = await self._next_research_query(user_query, enriched_query, all_pages, cycle, __request__, user_obj, research_model)
                if cycle_query in queries_used and cycle > 1:
                    break
                queries_used.append(cycle_query)
                await emitter.status(f"Research cycle {cycle}/{max_iterations}: {cycle_query[:120]}")

                ranked, scraped, failures, _variants = await self._search_and_scrape(cycle_query, emitter)
                search_failures.extend(failures)

                for row in ranked:
                    u = row.get("url")
                    if u and u not in seen_ranked:
                        all_ranked.append(row)
                        seen_ranked.add(u)

                new_pages = []
                for page in scraped:
                    u = page.get("url")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        new_pages.append(page)
                all_pages.extend(new_pages)

                if not new_pages and cycle >= self.valves.RESEARCH_MIN_ITERATIONS:
                    break
                if not await self._should_continue_research(user_query, all_pages, cycle, max_iterations, __request__, user_obj, research_model):
                    break

            if include_citations:
                for page in all_pages[:10]:
                    if page.get("url") and page.get("content"):
                        await emitter.citation(page.get("title", "Untitled"), page["url"], page["content"])

            output = {
                "query": user_query,
                "query_used": enriched_query,
                "mode": "research",
                "iterations_used": cycle,
                "queries_used": queries_used,
                "results_scraped": all_pages,
                "results_ranked": all_ranked,
                "sources_gathered": len(all_pages),
                "unique_urls": len(seen_urls),
                "research_context": self._build_research_context(all_pages),
                "deep_synthesis": None,
                "reasoning": {"mode_requested": requested_mode, "mode_selected": "research", "is_temporal_query": self._is_temporal_query(user_query), "search_failures": search_failures, "datetime_context": dt_info},
            }

            output["reasoning"]["mode_prefix_override"] = mode_prefix_override
            if show_reasoning:
                await emitter.message("\n".join(["### Unified Search Function v1.1 Reasoning", f"- Mode requested: {requested_mode}", f"- Queries used: {len(queries_used)}", f"- Sources gathered: {len(all_pages)}"]))
            if show_status:
                await emitter.status(f"Complete: mode=research, sources={len(all_pages)}, cycles={cycle}", status="complete", done=True)
            return ""

        fused, scraped, failures, variants = await self._search_and_scrape(enriched_query, emitter)
        avg_quality = sum(item.get("quality_score", 0.0) for item in scraped) / len(scraped) if scraped else 0.0
        coverage = sum(1 for item in scraped if len(item.get("content", "")) >= self.valves.MIN_CONTENT_CHARS)
        selected_mode = requested_mode
        if requested_mode == "auto":
            selected_mode = "deep" if (self._is_complex_query(user_query) or avg_quality < 0.28 or coverage < max(2, self.valves.PAGES_TO_SCRAPE // 2)) else "fast"

        deep_synthesis = None
        if selected_mode == "deep":
            mode_used, deep_synthesis = await self._vane_or_fast(enriched_query, "web", "balanced", emitter)
            selected_mode = mode_used

        if include_citations:
            for item in scraped[:8]:
                if item.get("url") and item.get("content"):
                    await emitter.citation(item.get("title", "Untitled"), item["url"], item["content"])

        if show_reasoning:
            await emitter.message("\n".join(["### Unified Search Function v1.1 Reasoning", f"- Mode requested: {requested_mode}", f"- Mode selected: {selected_mode}", f"- Query variants: {len(variants)}", f"- Avg source quality: {round(avg_quality, 3)}", f"- Coverage (usable pages): {coverage}"]))

        output = {
            "query": user_query,
            "query_used": enriched_query,
            "mode": selected_mode,
            "results_scraped": scraped,
            "results_ranked": fused,
            "deep_synthesis": deep_synthesis,
            "reasoning": {"mode_requested": requested_mode, "mode_selected": selected_mode, "is_complex_query": self._is_complex_query(user_query), "is_temporal_query": self._is_temporal_query(user_query), "query_variants": variants, "avg_quality": round(avg_quality, 3), "coverage": coverage, "search_failures": failures, "datetime_context": dt_info},
            "notes": {"fusion": "Reciprocal Rank Fusion used across query variants", "fallback": "Deep mode falls back to fast evidence if Vane fails", "research": "Research mode uses Open-WebUI model calls for follow-up query generation and continue/stop decisions"},
        }

        output["reasoning"]["mode_prefix_override"] = mode_prefix_override

        if show_status:
            await emitter.status(f"Complete: mode={selected_mode}, fused={len(fused)}, scraped={len(scraped)}", status="complete", done=True)
        return json.dumps(output, ensure_ascii=False)
