"""
title: Enhanced Websearch Tool
author: GitHub Copilot
version: 1.1.0
license: MIT
description: >
    Tool-oriented unified Open-WebUI web search capability that combines
    SearXNG retrieval with query expansion + reciprocal rank fusion, robust
    scraping (FlareSolverr/PDF), optional Vane deep synthesis, and iterative
    research mode with configurable follow-up planning backend.
requirements: beautifulsoup4, requests
"""

import asyncio
import concurrent.futures
import json
import logging
import re
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class EventEmitter:
    def __init__(self, event_emitter: Optional[Callable[[dict], Any]]):
        self.event_emitter = event_emitter

    async def status(
        self, description: str, status: str = "in_progress", done: bool = False
    ):
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
        return hits >= 2 or (
            response.status_code == 200 and len(response.text) < 2000 and hits >= 1
        )

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
        for tag in soup.find_all(
            ["script", "style", "noscript", "nav", "footer", "header", "aside", "iframe"]
        ):
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
            "html": "",
            "source": "direct",
            "error": None,
        }
        if not urlparse(url).scheme:
            url = "https://" + url
            result["url"] = url

        try:
            resp = self.session.get(
                url, timeout=self.valves.REQUEST_TIMEOUT, allow_redirects=True
            )
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

            result["html"] = html

            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            result["title"] = (
                title_tag.get_text(strip=True) if title_tag else urlparse(url).netloc
            )
            result["content"] = self._extract_text(html)

        except requests.RequestException as exc:
            result["error"] = str(exc)

        return result

    def extract_structure(self, url: str) -> Dict[str, Any]:
        out = {
            "url": url,
            "title": "",
            "meta": {},
            "headings": [],
            "links": [],
            "tables": [],
            "sections": [],
            "code_blocks": [],
            "lists": [],
            "source": "direct",
            "error": None,
        }

        page = self.scrape(url)
        out["source"] = page.get("source", "direct")
        out["error"] = page.get("error")
        out["title"] = page.get("title", "")
        if not page.get("content"):
            return out

        target_url = page.get("url", url)
        try:
            html = page.get("html", "")
            if not html:
                resp = self.session.get(target_url, timeout=self.valves.REQUEST_TIMEOUT)
                html = resp.text
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all(["script", "style", "noscript"]):
                tag.decompose()

            desc = soup.find("meta", attrs={"name": "description"})
            if desc and desc.get("content"):
                out["meta"]["description"] = desc["content"]
            canonical = soup.find("link", attrs={"rel": "canonical"})
            if canonical and canonical.get("href"):
                out["meta"]["canonical_url"] = canonical["href"]

            for tag in soup.find_all(re.compile(r"^h[1-6]$")):
                t = tag.get_text(strip=True)
                if t:
                    out["headings"].append(
                        {"level": int(tag.name[1]), "text": t, "id": tag.get("id", "")}
                    )

            seen_links = set()
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                absolute = urljoin(target_url, href)
                if absolute in seen_links:
                    continue
                seen_links.add(absolute)
                out["links"].append(
                    {"url": absolute, "text": tag.get_text(strip=True) or "[no text]"}
                )
            out["links"] = out["links"][:200]

            for idx, table in enumerate(soup.find_all("table")):
                headers = [h.get_text(strip=True) for h in table.find_all("th")]
                rows = []
                records = []
                for tr in table.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                    if cells and cells != headers:
                        rows.append(cells)
                        if headers and len(cells) == len(headers):
                            records.append(
                                {
                                    header if header else f"col_{i+1}": value
                                    for i, (header, value) in enumerate(zip(headers, cells))
                                }
                            )
                if headers or rows:
                    out["tables"].append(
                        {
                            "index": idx,
                            "headers": headers,
                            "rows": rows[:100],
                            "records": records[:100],
                        }
                    )
            out["tables"] = out["tables"][:20]

            for block in soup.find_all(["pre", "code"]):
                text = block.get_text(strip=True)
                if text and len(text) > 10:
                    out["code_blocks"].append({"language": "", "content": text[:5000]})
            out["code_blocks"] = out["code_blocks"][:30]

            for lst in soup.find_all(["ul", "ol"]):
                items = [
                    li.get_text(strip=True)[:500]
                    for li in lst.find_all("li", recursive=False)
                ]
                items = [i for i in items if i]
                if len(items) >= 2:
                    out["lists"].append(
                        {
                            "type": "ordered" if lst.name == "ol" else "unordered",
                            "items": items[:50],
                        }
                    )
            out["lists"] = out["lists"][:20]

            main = (
                soup.find("main")
                or soup.find("article")
                or soup.find("div", {"role": "main"})
                or soup.body
                or soup
            )
            section = {"heading": "", "heading_level": 0, "content": ""}
            for el in main.find_all(True, recursive=False):
                if re.match(r"^h[1-6]$", el.name):
                    if section["content"].strip():
                        out["sections"].append(section)
                    section = {
                        "heading": el.get_text(strip=True),
                        "heading_level": int(el.name[1]),
                        "content": "",
                    }
                elif el.name in ("p", "li", "blockquote", "dd", "figcaption"):
                    txt = el.get_text(strip=True)
                    if txt and len(txt) > 5:
                        section["content"] += txt + "\n"
            if section["content"].strip():
                out["sections"].append(section)

        except requests.RequestException as exc:
            out["error"] = str(exc)

        return out


class Tools:
    class Valves(BaseModel):
        SEARXNG_BASE_URL: str = Field(default="http://searxng:8080", description="SearXNG base URL")
        VANE_URL: str = Field(default="http://vane:3000", description="Vane base URL")
        FLARESOLVERR_URL: str = Field(
            default="http://flaresolverr:8191/v1",
            description="FlareSolverr endpoint; set empty to disable",
        )
        SEARCH_RESULTS_PER_QUERY: int = Field(default=8, ge=3, le=20)
        PAGES_TO_SCRAPE: int = Field(default=5, ge=1, le=12)
        ENABLE_VANE_DEEP: bool = Field(default=True, description="Allow deep synthesis via Vane")
        VANE_CHAT_MODEL_PROVIDER_ID: str = Field(default="", description="Vane chat provider ID")
        VANE_CHAT_MODEL_KEY: str = Field(default="auto-main", description="Vane chat model key")
        VANE_EMBEDDING_MODEL_PROVIDER_ID: str = Field(default="", description="Vane embedding provider ID")
        VANE_EMBEDDING_MODEL_KEY: str = Field(default="Xenova/nomic-embed-text-v1", description="Vane embedding model key")

        INTERNAL_DEFAULTS: ClassVar[Dict[str, Any]] = {
            "REQUEST_TIMEOUT": 15,
            "FLARESOLVERR_TIMEOUT": 60,
            "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "CONCURRENT_SCRAPE_WORKERS": 4,
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
            "VANE_TIMEOUT": 45,
            "RESEARCH_MIN_ITERATIONS": 2,
            "RESEARCH_MAX_CONTEXT_SOURCES": 20,
            "IGNORED_DOMAINS": "",
        }

        def __getattr__(self, name: str) -> Any:
            if name in self.INTERNAL_DEFAULTS:
                return self.INTERNAL_DEFAULTS[name]
            raise AttributeError(name)

    class UserValves(BaseModel):
        mode: str = Field(
            default="auto", description="auto, fast, deep, research"
        )
        show_status_updates: bool = Field(default=True)
        include_citations: bool = Field(default=True)
        show_reasoning: bool = Field(default=True)
        max_iterations: int = Field(
            default=5,
            ge=1,
            le=10,
            description="Max research cycles in research mode",
        )

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
        clean_query = {
            k: v
            for k, v in query.items()
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        }
        netloc = parsed.netloc.lower()
        path = parsed.path or "/"
        return urlunparse(
            (parsed.scheme or "https", netloc, path, "", urlencode(clean_query, doseq=True), "")
        )

    def _ignored_domains(self) -> set:
        if not self.valves.IGNORED_DOMAINS.strip():
            return set()
        return {
            d.strip().lower()
            for d in self.valves.IGNORED_DOMAINS.split(",")
            if d.strip()
        }

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
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) >= 3 and token not in stopwords
        }

    def _evidence_coverage(self, original_query: str, pages: List[Dict[str, Any]]) -> float:
        query_terms = self._term_signature(original_query)
        if not query_terms or not pages:
            return 0.0
        recent_text = " ".join(
            f"{page.get('title', '')} {page.get('content', '')[:1000]}" for page in pages[-6:]
        )
        content_terms = self._term_signature(recent_text)
        if not content_terms:
            return 0.0
        return len(query_terms & content_terms) / max(1, len(query_terms))

    def _evidence_redundancy(self, pages: List[Dict[str, Any]]) -> float:
        if len(pages) < 2:
            return 0.0
        signatures = [
            self._term_signature(f"{page.get('title', '')} {page.get('content', '')[:1200]}")
            for page in pages[-5:]
        ]
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
        page_terms = self._term_signature(
            " ".join(
                f"{page.get('title', '')} {page.get('content', '')[:1000]}" for page in pages[-6:]
            )
        )
        missing = sorted(query_terms - page_terms)
        return missing[:5]

    def _is_temporal_query(self, query: str) -> bool:
        temporal = [
            r"\btoday\b",
            r"\btomorrow\b",
            r"\byesterday\b",
            r"\bcurrent\b",
            r"\blatest\b",
            r"\brecent\b",
            r"\bnews\b",
            r"\bweather\b",
            r"\bthis\s+(week|month|year)\b",
            r"\bnext\s+(week|month|year)\b",
        ]
        ql = query.lower()
        return any(re.search(p, ql) for p in temporal)

    def _inject_temporal_context(self, query: str) -> Tuple[str, Dict[str, str]]:
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo(self.valves.TIMEZONE))
        except Exception:
            now = datetime.utcnow()

        info = {
            "date": now.strftime("%Y-%m-%d"),
            "day_name": now.strftime("%A"),
            "month_name": now.strftime("%B"),
            "year": now.strftime("%Y"),
            "time": now.strftime("%H:%M:%S"),
            "timezone": self.valves.TIMEZONE,
            "formatted": now.strftime(self.valves.DATETIME_FORMAT),
        }

        if not self.valves.INJECT_DATETIME or not self._is_temporal_query(query):
            return query, info

        enriched = f"{info['date']} ({info['day_name']}): {query}"
        ql = query.lower()
        if "tomorrow" in ql:
            tmr = now + timedelta(days=1)
            enriched = (
                f"{info['date']} (today) -> {tmr.strftime('%Y-%m-%d')} "
                f"{tmr.strftime('%A')} (tomorrow): {query}"
            )
        elif "yesterday" in ql:
            yst = now - timedelta(days=1)
            enriched = (
                f"{info['date']} (today) -> {yst.strftime('%Y-%m-%d')} "
                f"{yst.strftime('%A')} (yesterday): {query}"
            )
        return enriched, info

    def _expand_queries(self, query: str) -> List[str]:
        variants = [query]
        q = query.strip()
        if len(q.split()) > 3:
            variants.append(f"{q} overview")
            variants.append(f"{q} official documentation")
        if re.search(r"\b(compare|vs|versus|difference|best|alternatives?)\b", q.lower()):
            variants.append(f"{q} benchmark")
            variants.append(f"{q} pros cons")
        if self._is_temporal_query(q):
            variants.append(f"{q} latest updates")

        deduped = []
        seen = set()
        for item in variants:
            k = item.strip().lower()
            if k and k not in seen:
                seen.add(k)
                deduped.append(item.strip())
        return deduped[: self.valves.QUERY_VARIANTS_LIMIT]

    def _resolve_auto_mode(self, query: str, avg_quality: float, coverage: int) -> str:
        if self._is_complex_query(query):
            return "deep"
        if avg_quality < 0.28:
            return "deep"
        if coverage < max(2, self.valves.PAGES_TO_SCRAPE // 2):
            return "deep"
        return "fast"

    def _apply_mode_prefix(self, query: str, requested_mode: str) -> Tuple[str, str, Optional[str]]:
        match = re.match(r"^\s*(fast|deep)\s*:\s*(.*)$", query, flags=re.IGNORECASE)
        if not match:
            return query, requested_mode, None

        forced_mode = match.group(1).lower()
        stripped_query = match.group(2).strip()
        if stripped_query:
            return stripped_query, forced_mode, forced_mode
        return query, forced_mode, forced_mode

    def _is_complex_query(self, query: str) -> bool:
        ql = query.lower()
        if len(query.split()) > 15:
            return True
        patterns = [
            r"\bcompare\b",
            r"\bvs\b",
            r"\bdeep\b",
            r"\bresearch\b",
            r"\btrade[- ]?offs\b",
            r"\bpros\b",
            r"\bcons\b",
            r"\barchitecture\b",
            r"\bdesign\b",
            r"\bhow\s+(to|does|can|should|could)\b",
            r"\bwhy\s+(does|do|is|are)\b",
        ]
        return any(re.search(p, ql) for p in patterns)

    def _source_quality_score(self, url: str, title: str, text: str) -> float:
        score = 0.0
        domain = urlparse(url).netloc.lower()
        if any(domain.endswith(tld) for tld in [".edu", ".gov", ".org"]):
            score += 0.25
        if any(
            token in domain
            for token in ["github.com", "arxiv.org", "wikipedia.org", "docs", "developer"]
        ):
            score += 0.2
        if len(text) > 1500:
            score += 0.25
        if len(text) > 5000:
            score += 0.1
        if re.search(
            r"\bupdated\b|\bpublished\b|\b\d{4}\b", (title + " " + text)[:1200].lower()
        ):
            score += 0.1
        if re.search(r"\bsubscribe\b|\bsign in\b|\bcookie\b", text[:800].lower()):
            score -= 0.1
        return max(0.0, min(1.0, score))

    def _search_searxng(self, query: str) -> List[Dict[str, Any]]:
        params = {
            "q": query,
            "format": "json",
            "number_of_results": self.valves.SEARCH_RESULTS_PER_QUERY,
            "categories": self.valves.SEARCH_CATEGORIES,
            "language": self.valves.SEARCH_LANGUAGE,
        }
        if self.valves.SEARCH_TIME_RANGE:
            params["time_range"] = self.valves.SEARCH_TIME_RANGE
        if self.valves.SEARCH_ENGINES:
            params["engines"] = self.valves.SEARCH_ENGINES

        url = f"{self.valves.SEARXNG_BASE_URL.rstrip('/')}/search"
        resp = requests.get(url, params=params, timeout=self.valves.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _rrf_fuse(self, result_sets: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        k = self.valves.RRF_K
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

                scores[normalized] = scores.get(normalized, 0.0) + (1.0 / (k + rank))
                if normalized not in merged:
                    merged[normalized] = {
                        "url": normalized,
                        "title": item.get("title", "Untitled"),
                        "snippet": item.get("content", ""),
                        "engines": item.get("engines", []),
                    }

        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        fused = []
        for url, score in ranked:
            doc = merged[url]
            doc["rrf_score"] = round(score, 6)
            fused.append(doc)
        return fused

    async def _vane_deep_search(
        self, query: str, source_mode: str, depth: str
    ) -> Dict[str, Any]:
        if not self.valves.ENABLE_VANE_DEEP:
            return {"enabled": False, "error": "Vane deep search is disabled"}

        if (
            not self.valves.VANE_CHAT_MODEL_PROVIDER_ID
            or not self.valves.VANE_EMBEDDING_MODEL_PROVIDER_ID
        ):
            return {"enabled": False, "error": "Vane provider IDs are not configured"}

        source_map = {
            "web": ["web"],
            "academia": ["academic"],
            "social": ["discussions"],
            "all": ["web", "academic", "discussions"],
        }
        optimization_map = {
            "quick": "speed",
            "speed": "speed",
            "balanced": "balanced",
            "quality": "quality",
        }

        payload = {
            "query": query,
            "sources": source_map.get(source_mode, ["web"]),
            "optimizationMode": optimization_map.get(depth, "balanced"),
            "stream": False,
            "chatModel": {
                "providerId": self.valves.VANE_CHAT_MODEL_PROVIDER_ID,
                "key": self.valves.VANE_CHAT_MODEL_KEY,
            },
            "embeddingModel": {
                "providerId": self.valves.VANE_EMBEDDING_MODEL_PROVIDER_ID,
                "key": self.valves.VANE_EMBEDDING_MODEL_KEY,
            },
        }

        try:
            resp = requests.post(
                f"{self.valves.VANE_URL.rstrip('/')}/api/search",
                json=payload,
                timeout=self.valves.VANE_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            sources = []
            for src in data.get("sources", []):
                meta = src.get("metadata", {})
                sources.append(
                    {
                        "title": meta.get("title", "Untitled"),
                        "url": meta.get("url", ""),
                        "content": src.get("content", ""),
                    }
                )
            return {
                "enabled": True,
                "message": data.get("message", ""),
                "sources": sources,
            }
        except Exception as exc:
            return {"enabled": True, "error": str(exc), "sources": []}

    def _build_research_context(self, pages: List[Dict[str, Any]]) -> str:
        cap = self.valves.RESEARCH_MAX_CONTEXT_SOURCES
        chunks = []
        for page in pages[:cap]:
            chunks.append(
                f"=== SOURCE: {page.get('title', 'Untitled')} ({page.get('url', '')}) ===\n"
                f"{page.get('content', '')[:2000]}"
            )
        return "\n\n".join(chunks)

    def _sentence_fragments(self, text: str) -> List[str]:
        if not text:
            return []
        parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
        return [p.strip() for p in parts if len(p.strip()) >= 30]

    def _fast_bullets(self, pages: List[Dict[str, Any]], limit: int = 8) -> List[str]:
        bullets: List[str] = []
        seen = set()
        for page in pages[:limit]:
            title = (page.get("title") or "Untitled").strip()
            raw = page.get("snippet") or page.get("content") or ""
            fragments = self._sentence_fragments(raw)
            if not fragments:
                continue
            bullet = f"{title}: {fragments[0]}"
            key = bullet.lower()
            if key in seen:
                continue
            seen.add(key)
            bullets.append(bullet)
        return bullets

    def _vane_bullets(self, deep_synthesis: Optional[Dict[str, Any]], limit: int = 8) -> List[str]:
        if not deep_synthesis:
            return []
        message = deep_synthesis.get("message", "") or ""
        if not message:
            return []
        lines: List[str] = []
        for chunk in message.splitlines():
            line = chunk.strip()
            if not line:
                continue
            line = re.sub(r"^[-*#\d.\)\s]+", "", line).strip()
            if len(line) >= 30:
                lines.append(line)
        if not lines:
            lines = self._sentence_fragments(message)
        deduped: List[str] = []
        seen = set()
        for line in lines:
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(line)
        return deduped[:limit]

    def _term_overlap(self, left: str, right: str) -> float:
        a = self._term_signature(left)
        b = self._term_signature(right)
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    def _fuse_deep_signals(
        self,
        fast_pages: List[Dict[str, Any]],
        deep_synthesis: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not deep_synthesis or deep_synthesis.get("error"):
            return {
                "enabled": False,
                "reason": "deep_synthesis_unavailable",
                "consensus": [],
                "fast_additions": [],
                "vane_additions": [],
            }

        fast = self._fast_bullets(fast_pages)
        vane = self._vane_bullets(deep_synthesis)
        consensus = []
        vane_only = []
        matched_fast = set()

        for vb in vane:
            best_idx = -1
            best_score = 0.0
            for idx, fb in enumerate(fast):
                score = self._term_overlap(vb, fb)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx >= 0 and best_score >= 0.22:
                matched_fast.add(best_idx)
                consensus.append(
                    {
                        "vane": vb,
                        "fast": fast[best_idx],
                        "overlap": round(best_score, 3),
                    }
                )
            else:
                vane_only.append(vb)

        fast_only = [fb for idx, fb in enumerate(fast) if idx not in matched_fast]

        return {
            "enabled": True,
            "reason": "ok",
            "consensus": consensus[:5],
            "fast_additions": fast_only[:5],
            "vane_additions": vane_only[:5],
        }

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

    def _next_research_query(
        self,
        original_query: str,
        enriched_query: str,
        pages: List[Dict[str, Any]],
        iterations_used: int,
    ) -> str:
        if iterations_used == 1:
            return enriched_query

        return self._heuristic_followup_query(original_query, pages)

    def _should_continue_research(
        self,
        original_query: str,
        pages: List[Dict[str, Any]],
        cycle: int,
        max_iterations: int,
    ) -> bool:
        if cycle < self.valves.RESEARCH_MIN_ITERATIONS:
            return True

        coverage = self._evidence_coverage(original_query, pages)
        redundancy = self._evidence_redundancy(pages)
        if coverage >= 0.65 and redundancy >= 0.65:
            return False
        if coverage >= 0.8:
            return redundancy < 0.8 and len(pages) < max_iterations * 2

        # Heuristic fallback: continue while evidence is still sparse or diverse.
        return len(pages) < max(6, cycle * 2) or coverage < 0.5 or redundancy < 0.55

    async def _search_and_scrape(
        self,
        query: str,
        emitter: EventEmitter,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
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
        top_for_scrape = fused[: self.valves.PAGES_TO_SCRAPE]

        await emitter.status(
            f"Fused {sum(len(rs) for rs in result_sets)} raw results -> {len(fused)} unique URLs"
        )

        scraper = PageScraper(self.valves)
        scraped: List[Dict[str, Any]] = []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.valves.CONCURRENT_SCRAPE_WORKERS
        ) as executor:
            futures = {
                executor.submit(scraper.scrape, item["url"]): item for item in top_for_scrape
            }
            for future in concurrent.futures.as_completed(futures):
                base = futures[future]
                try:
                    page = future.result()
                    text = page.get("content", "")
                    if text and len(text) < self.valves.MIN_CONTENT_CHARS:
                        text = ""
                    score = self._source_quality_score(
                        page.get("url", base["url"]),
                        page.get("title", base["title"]),
                        text,
                    )
                    scraped.append(
                        {
                            "url": page.get("url", base["url"]),
                            "title": page.get("title", base["title"]),
                            "snippet": base.get("snippet", ""),
                            "content": text or base.get("snippet", ""),
                            "source": page.get("source", "direct"),
                            "error": page.get("error"),
                            "rrf_score": base.get("rrf_score", 0.0),
                            "quality_score": score,
                        }
                    )
                except Exception as exc:
                    scraped.append(
                        {
                            "url": base.get("url", ""),
                            "title": base.get("title", ""),
                            "snippet": base.get("snippet", ""),
                            "content": base.get("snippet", ""),
                            "source": "failed",
                            "error": str(exc),
                            "rrf_score": base.get("rrf_score", 0.0),
                            "quality_score": 0.0,
                        }
                    )

        scraped = sorted(
            scraped,
            key=lambda x: (x.get("quality_score", 0.0), x.get("rrf_score", 0.0)),
            reverse=True,
        )

        return fused, scraped, failures, variants

    async def elevated_search(
        self,
        query: str,
        mode: str = "auto",
        source_mode: str = "web",
        depth: str = "balanced",
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Unified high-performance web search for Open-WebUI (v1.1).

        Modes:
        - auto: choose between fast and deep based on complexity and evidence quality
        - fast: SearXNG + query variants + RRF + scraping
        - deep: fast pipeline + Vane synthesis with fast fallback on failure
        - research: iterative search/scrape/refine cycles for richer evidence
        """
        emitter = EventEmitter(__event_emitter__)

        user_valves = __user__.get("valves") if __user__ and __user__.get("valves") else None
        show_status = self._cfg("show_status_updates", user_valves, True)
        include_citations = self._cfg("include_citations", user_valves, True)
        show_reasoning = self._cfg("show_reasoning", user_valves, True)
        requested_mode = mode or self._cfg("mode", user_valves, "auto")
        max_iterations = self._cfg("max_iterations", user_valves, 5)
        query, requested_mode, mode_prefix_override = self._apply_mode_prefix(
            query, requested_mode
        )

        if show_status:
            await emitter.status(f"Starting elevated search v1.1: {query}")

        enriched_query, dt_info = self._inject_temporal_context(query)

        # ------------------------------------------------------------------
        # RESEARCH MODE
        # ------------------------------------------------------------------
        if requested_mode == "research":
            await emitter.status(f"Research mode: up to {max_iterations} cycles")

            all_pages: List[Dict[str, Any]] = []
            all_ranked: List[Dict[str, Any]] = []
            queries_used: List[str] = []
            seen_urls = set()
            search_failures: List[str] = []

            cycle = 0
            while cycle < max_iterations:
                cycle += 1
                cycle_query = self._next_research_query(
                    query, enriched_query, all_pages, cycle
                )
                if cycle_query in queries_used and cycle > 1:
                    break
                queries_used.append(cycle_query)

                await emitter.status(f"Research cycle {cycle}/{max_iterations}: {cycle_query[:120]}")

                ranked, scraped, failures, _variants = await self._search_and_scrape(
                    cycle_query, emitter
                )
                search_failures.extend(failures)

                for row in ranked:
                    u = row.get("url")
                    if u and u not in seen_urls:
                        all_ranked.append(row)

                new_pages = []
                for page in scraped:
                    u = page.get("url")
                    if not u or u in seen_urls:
                        continue
                    seen_urls.add(u)
                    new_pages.append(page)

                all_pages.extend(new_pages)
                await emitter.status(
                    f"Cycle {cycle}: +{len(new_pages)} pages, total {len(all_pages)}"
                )

                if not new_pages and cycle >= self.valves.RESEARCH_MIN_ITERATIONS:
                    break

                if not self._should_continue_research(query, all_pages, cycle, max_iterations):
                    await emitter.status(f"Research stopping after {cycle} cycles")
                    break

            if include_citations:
                for page in all_pages[:10]:
                    if page.get("url") and page.get("content"):
                        await emitter.citation(
                            page.get("title", "Untitled"), page["url"], page["content"]
                        )

            output = {
                "query": query,
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
                "reasoning": {
                    "mode_requested": requested_mode,
                    "mode_selected": "research",
                    "mode_prefix_override": mode_prefix_override,
                    "is_temporal_query": self._is_temporal_query(query),
                    "search_failures": search_failures,
                    "datetime_context": dt_info,
                },
            }

            if show_status:
                await emitter.status(
                    f"Complete: mode=research, sources={len(all_pages)}, cycles={cycle}",
                    status="complete",
                    done=True,
                )
            return json.dumps(output, ensure_ascii=False)

        # ------------------------------------------------------------------
        # FAST / DEEP / AUTO
        # ------------------------------------------------------------------
        fused, scraped, failures, variants = await self._search_and_scrape(
            enriched_query, emitter
        )

        if not fused:
            output = {
                "query": query,
                "query_used": enriched_query,
                "mode": "fast" if requested_mode == "auto" else requested_mode,
                "results_scraped": [],
                "results_ranked": [],
                "deep_synthesis": None,
                "reasoning": {
                    "mode_requested": requested_mode,
                    "mode_selected": "fast" if requested_mode == "auto" else requested_mode,
                    "mode_prefix_override": mode_prefix_override,
                    "search_failures": failures,
                    "query_variants": variants,
                    "datetime_context": dt_info,
                },
                "error": "No search results returned from SearXNG across all query variants",
            }
            if show_status:
                await emitter.status(
                    "No search results returned from upstream SearXNG",
                    status="error",
                    done=True,
                )
            return json.dumps(output, ensure_ascii=False)

        avg_quality = (
            sum(item.get("quality_score", 0.0) for item in scraped) / len(scraped)
            if scraped
            else 0.0
        )
        coverage = sum(
            1
            for item in scraped
            if len(item.get("content", "")) >= self.valves.MIN_CONTENT_CHARS
        )
        selected_mode = requested_mode
        if requested_mode == "auto":
            selected_mode = self._resolve_auto_mode(query, avg_quality, coverage)

        complex_query = self._is_complex_query(query)

        deep_synthesis = None
        deep_fusion = None
        if selected_mode == "deep":
            if show_status:
                await emitter.status("Escalating to deep synthesis via Vane")
            deep_synthesis = await self._vane_deep_search(enriched_query, source_mode, depth)
            deep_fusion = self._fuse_deep_signals(scraped, deep_synthesis)

            # Best-of-both behavior: if deep fails, keep fast evidence as fallback.
            if deep_synthesis and deep_synthesis.get("error"):
                selected_mode = "fast_fallback"
                if show_status:
                    await emitter.status(
                        "Vane deep synthesis failed; returning fast pipeline evidence"
                    )

        if include_citations:
            for item in scraped[:8]:
                if item.get("url") and item.get("content"):
                    await emitter.citation(
                        item.get("title", "Untitled"), item["url"], item["content"]
                    )

        reasoning = {
            "mode_requested": requested_mode,
            "mode_selected": selected_mode,
            "mode_prefix_override": mode_prefix_override,
            "is_complex_query": complex_query,
            "is_temporal_query": self._is_temporal_query(query),
            "query_variants": variants,
            "avg_quality": round(avg_quality, 3),
            "coverage": coverage,
            "search_failures": failures,
            "datetime_context": dt_info,
            "rrf_k": self.valves.RRF_K,
        }

        if show_reasoning:
            await emitter.message(
                "\n".join(
                    [
                        "### Elevated Search v1.1 Reasoning",
                        f"- Mode requested: {requested_mode}",
                        f"- Mode selected: {selected_mode}",
                        f"- Query variants: {len(variants)}",
                        f"- Avg source quality: {round(avg_quality, 3)}",
                        f"- Coverage (usable pages): {coverage}",
                    ]
                )
            )

        output = {
            "query": query,
            "query_used": enriched_query,
            "mode": selected_mode,
            "source_mode": source_mode,
            "depth": depth,
            "results_scraped": scraped,
            "results_ranked": fused,
            "deep_synthesis": deep_synthesis,
            "deep_fusion": deep_fusion,
            "reasoning": reasoning,
            "notes": {
                "fusion": "Reciprocal Rank Fusion used across query variants",
                "fallback": "Deep mode falls back to fast evidence if Vane fails",
                "research": "Use mode=research for iterative search/scrape/refine cycles",
            },
        }

        if show_status:
            await emitter.status(
                f"Complete: mode={selected_mode}, fused={len(fused)}, scraped={len(scraped)}",
                status="complete",
                done=True,
            )

        return json.dumps(output, ensure_ascii=False)

    async def fetch_page(
        self,
        url: str,
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        if __user__:
            _ = __user__
        await emitter.status(f"Fetching page: {url}")
        scraper = PageScraper(self.valves)
        loop = asyncio.get_event_loop()
        page = await loop.run_in_executor(None, scraper.scrape, url)
        await emitter.status("Fetch complete", status="complete", done=True)
        return json.dumps(page, ensure_ascii=False)

    async def extract_page_structure(
        self,
        url: str,
        components: str = "all",
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        if __user__:
            _ = __user__

        await emitter.status(f"Extracting structure: {url}")
        scraper = PageScraper(self.valves)
        loop = asyncio.get_event_loop()
        structure = await loop.run_in_executor(None, scraper.extract_structure, url)

        all_components = {
            "headings",
            "links",
            "tables",
            "sections",
            "code_blocks",
            "lists",
            "meta",
        }
        if components.strip().lower() == "all":
            requested = all_components
        else:
            requested = {c.strip().lower() for c in components.split(",") if c.strip()}
            requested = requested & all_components

        out = {
            "url": structure.get("url", url),
            "title": structure.get("title", ""),
            "source": structure.get("source", "direct"),
            "error": structure.get("error"),
        }
        for key in requested:
            out[key] = structure.get(key, [] if key != "meta" else {})

        out["summary"] = {}
        for key in requested:
            data = out.get(key)
            if isinstance(data, list):
                out["summary"][key] = len(data)
            elif isinstance(data, dict):
                out["summary"][key] = len(data)

        await emitter.status("Structure extraction complete", status="complete", done=True)
        return json.dumps(out, ensure_ascii=False)
