"""
title: Enhanced Websearch Tool
author: GitHub Copilot
version: 1.2.0
license: MIT
description: >
    Tool-oriented unified Open-WebUI web search capability that combines
    SearXNG retrieval with query expansion + reciprocal rank fusion, robust
    scraping (FlareSolverr/PDF), optional Vane deep synthesis, and iterative
    research mode.
requirements: pydantic
optional_requirements: beautifulsoup4, requests, pypdf or PyPDF2
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html import unescape
from io import BytesIO
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Optional runtime dependencies
# ---------------------------------------------------------------------------

try:
    import requests as _requests

    REQUESTS_AVAILABLE = True
except Exception:
    _requests = None
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup as _BeautifulSoup

    BS4_AVAILABLE = True
except Exception:
    _BeautifulSoup = None
    BS4_AVAILABLE = False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_VANE_PATTERNS = {
    "i cannot",
    "i can't",
    "unable to",
    "do not have access",
    "as an ai",
    "insufficient information",
    "not enough information",
    "can't access",
    "cannot access",
}

STOPWORDS: Set[str] = {
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


# ---------------------------------------------------------------------------
# Config and valves
# ---------------------------------------------------------------------------

def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


class Valves(BaseModel):
    SEARXNG_BASE_URL: str = Field(default_factory=lambda: _env_str("SEARXNG_URL", _env_str("SEARXNG_BASE_URL", "http://searxng:8080")), description="SearXNG base URL")
    VANE_URL: str = Field(default_factory=lambda: _env_str("VANE_URL", "http://vane:3000"), description="Vane base URL")
    FLARESOLVERR_URL: str = Field(
        default_factory=lambda: _env_str("FLARESOLVERR_URL", "http://flaresolverr:8191/v1"),
        description="FlareSolverr endpoint; set empty to disable",
    )
    SEARCH_RESULTS_PER_QUERY: int = Field(default_factory=lambda: _env_int("SEARCH_RESULTS_PER_QUERY", 8, 3, 20), ge=3, le=20)
    PAGES_TO_SCRAPE: int = Field(default_factory=lambda: _env_int("PAGES_TO_SCRAPE", 5, 1, 12), ge=1, le=12)
    ENABLE_VANE_DEEP: bool = Field(default_factory=lambda: _env_bool("ENABLE_VANE_DEEP", True), description="Allow deep synthesis via Vane")
    VANE_CHAT_MODEL_PROVIDER_ID: str = Field(default_factory=lambda: _env_str("VANE_CHAT_MODEL_PROVIDER_ID", ""), description="Vane chat provider ID")
    VANE_CHAT_MODEL_KEY: str = Field(default_factory=lambda: _env_str("VANE_CHAT_MODEL_KEY", "auto-main"), description="Vane chat model key")
    VANE_EMBEDDING_MODEL_PROVIDER_ID: str = Field(default_factory=lambda: _env_str("VANE_EMBEDDING_MODEL_PROVIDER_ID", ""), description="Vane embedding provider ID")
    VANE_EMBEDDING_MODEL_KEY: str = Field(default_factory=lambda: _env_str("VANE_EMBEDDING_MODEL_KEY", "Xenova/nomic-embed-text-v1"), description="Vane embedding model key")
    STRICT_COMPAT_MODE: bool = Field(default_factory=lambda: _env_bool("STRICT_COMPAT_MODE", False), description="Use strict compatibility mode for constrained runtimes")

    INTERNAL_DEFAULTS: ClassVar[Dict[str, Any]] = {
        "REQUEST_TIMEOUT": _env_int("REQUEST_TIMEOUT", 15, 3, 300),
        "FLARESOLVERR_TIMEOUT": _env_int("FLARESOLVERR_TIMEOUT", 60, 5, 300),
        "USER_AGENT": _env_str("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "CONCURRENT_SCRAPE_WORKERS": _env_int("CONCURRENT_SCRAPE_WORKERS", 4, 1, 12),
        "QUERY_VARIANTS_LIMIT": _env_int("QUERY_VARIANTS_LIMIT", 4, 1, 8),
        "RRF_K": _env_int("RRF_K", 60, 1, 200),
        "SEARCH_CATEGORIES": _env_str("SEARCH_CATEGORIES", "general"),
        "SEARCH_ENGINES": _env_str("SEARCH_ENGINES", ""),
        "SEARCH_LANGUAGE": _env_str("SEARCH_LANGUAGE", "en"),
        "SEARCH_TIME_RANGE": _env_str("SEARCH_TIME_RANGE", ""),
        "MAX_PAGE_CONTENT_CHARS": _env_int("MAX_PAGE_CONTENT_CHARS", 25000, 2000, 200000),
        "MIN_CONTENT_CHARS": _env_int("MIN_CONTENT_CHARS", 80, 20, 2000),
        "INJECT_DATETIME": _env_bool("INJECT_DATETIME", True),
        "DATETIME_FORMAT": _env_str("DATETIME_FORMAT", "%Y-%m-%d %A %B %d"),
        "TIMEZONE": _env_str("TIMEZONE", "UTC"),
        "VANE_TIMEOUT": _env_int("VANE_TIMEOUT", 90, 10, 300),
        "RESEARCH_MIN_ITERATIONS": _env_int("RESEARCH_MIN_ITERATIONS", 2, 1, 10),
        "RESEARCH_MAX_CONTEXT_SOURCES": _env_int("RESEARCH_MAX_CONTEXT_SOURCES", 20, 5, 60),
        "IGNORED_DOMAINS": _env_str("IGNORED_DOMAINS", ""),
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


# ---------------------------------------------------------------------------
# Runtime compatibility and transport
# ---------------------------------------------------------------------------

class RuntimeHttpError(Exception):
    pass


class RuntimeReadTimeout(RuntimeHttpError):
    pass


class RuntimeResponse:
    def __init__(self, status_code: int, headers: Dict[str, str], content: bytes, url: str):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeHttpError(f"HTTP {self.status_code} for {self.url}")


class RuntimeHttpClient:
    def __init__(self, use_requests: bool, default_headers: Optional[Dict[str, str]] = None):
        self.use_requests = use_requests and REQUESTS_AVAILABLE
        self.default_headers = default_headers or {}
        self.session = _requests.Session() if self.use_requests else None
        if self.session and self.default_headers:
            self.session.headers.update(self.default_headers)

    def _build_url(self, url: str, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode(params, doseq=True)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{query}"

    def _request_stdlib(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = 15,
        headers: Optional[Dict[str, str]] = None,
    ) -> RuntimeResponse:
        target = self._build_url(url, params)
        merged_headers = dict(self.default_headers)
        merged_headers.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            merged_headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            target,
            data=data,
            headers=merged_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return RuntimeResponse(
                    status_code=getattr(resp, "status", 200),
                    headers=dict(resp.headers.items()),
                    content=body,
                    url=target,
                )
        except TimeoutError as exc:
            raise RuntimeReadTimeout(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            return RuntimeResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                content=body,
                url=target,
            )
        except Exception as exc:
            raise RuntimeHttpError(str(exc)) from exc

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 15,
        headers: Optional[Dict[str, str]] = None,
    ) -> RuntimeResponse:
        if self.use_requests and self.session:
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    timeout=timeout,
                    allow_redirects=True,
                    headers=headers,
                )
                return RuntimeResponse(resp.status_code, dict(resp.headers), resp.content, resp.url)
            except _requests.exceptions.ReadTimeout as exc:
                raise RuntimeReadTimeout(str(exc)) from exc
            except Exception as exc:
                raise RuntimeHttpError(str(exc)) from exc
        return self._request_stdlib("GET", url, params=params, timeout=timeout, headers=headers)

    def post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        timeout: int = 15,
        headers: Optional[Dict[str, str]] = None,
    ) -> RuntimeResponse:
        if self.use_requests:
            try:
                resp = _requests.post(url, json=payload, timeout=timeout, headers=headers)
                return RuntimeResponse(resp.status_code, dict(resp.headers), resp.content, resp.url)
            except _requests.exceptions.ReadTimeout as exc:
                raise RuntimeReadTimeout(str(exc)) from exc
            except Exception as exc:
                raise RuntimeHttpError(str(exc)) from exc
        return self._request_stdlib("POST", url, payload=payload, timeout=timeout, headers=headers)


class RuntimeCompatibility:
    def __init__(self, strict_mode: bool):
        self.strict_mode = strict_mode
        self.transport_backend = "stdlib-urllib" if strict_mode or not REQUESTS_AVAILABLE else "requests"
        self.html_backend = "stdlib-regex" if strict_mode or not BS4_AVAILABLE else "beautifulsoup4"
        self.thread_backend = "sequential" if strict_mode else "threadpool"
        self.pdf_backend = self._detect_pdf_backend()

    def _detect_pdf_backend(self) -> str:
        if self.strict_mode:
            return "disabled"
        try:
            import pypdf  # noqa: F401

            return "pypdf"
        except Exception:
            pass
        try:
            import PyPDF2  # noqa: F401

            return "PyPDF2"
        except Exception:
            return "none"

    def use_threadpool(self) -> bool:
        return self.thread_backend == "threadpool"

    def html_parser_available(self) -> bool:
        return self.html_backend == "beautifulsoup4"

    def diagnostics(self) -> Dict[str, Any]:
        degraded_reasons = []
        if self.transport_backend != "requests":
            degraded_reasons.append("requests unavailable or strict mode enabled; using urllib transport")
        if self.html_backend != "beautifulsoup4":
            degraded_reasons.append("beautifulsoup4 unavailable or strict mode enabled; using basic HTML parsing")
        if self.pdf_backend in {"none", "disabled"}:
            degraded_reasons.append("PDF text extraction backend unavailable or disabled")
        if not self.use_threadpool():
            degraded_reasons.append("threadpool disabled; scraping runs sequentially")

        return {
            "strict_mode": self.strict_mode,
            "transport_backend": self.transport_backend,
            "html_backend": self.html_backend,
            "pdf_backend": self.pdf_backend,
            "thread_backend": self.thread_backend,
            "degraded": len(degraded_reasons) > 0,
            "degraded_reasons": degraded_reasons,
        }


# ---------------------------------------------------------------------------
# Fetching and extraction
# ---------------------------------------------------------------------------

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

    def __init__(self, valves: Valves, runtime: RuntimeCompatibility):
        self.valves = valves
        self.runtime = runtime
        self.http = RuntimeHttpClient(
            use_requests=runtime.transport_backend == "requests",
            default_headers={
                "User-Agent": self.valves.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )

    def _looks_blocked(self, response: RuntimeResponse) -> bool:
        if response.status_code in (403, 429, 503):
            return True
        body = response.text[:5000].lower()
        hits = sum(1 for marker in self.CAPTCHA_INDICATORS if marker in body)
        return hits >= 2 or (response.status_code == 200 and len(response.text) < 2000 and hits >= 1)

    def _is_pdf(self, url: str, response: Optional[RuntimeResponse] = None) -> bool:
        if url.lower().endswith(".pdf"):
            return True
        if response and "application/pdf" in response.headers.get("Content-Type", "").lower():
            return True
        return False

    def _extract_pdf_text(self, raw: bytes) -> str:
        if self.runtime.pdf_backend == "disabled":
            return "[PDF extraction disabled in strict compatibility mode]"
        try:
            import pypdf

            reader = pypdf.PdfReader(BytesIO(raw))
            parts = [page.extract_text() for page in reader.pages if page.extract_text()]
            if parts:
                return "\n\n".join(parts)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("pypdf extraction failed: %s", exc)

        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(BytesIO(raw))
            parts = [page.extract_text() for page in reader.pages if page.extract_text()]
            if parts:
                return "\n\n".join(parts)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("PyPDF2 extraction failed: %s", exc)

        return "[PDF detected but no PDF extraction backend is available]"

    def _clip_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _simple_html_to_text(self, html: str) -> str:
        cleaned = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
        cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"(?is)</p>|</div>|</li>|</section>|</article>", "\n", cleaned)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = unescape(cleaned)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return "\n".join(lines)

    def _extract_title_fallback(self, html: str, default: str) -> str:
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        if not match:
            return default
        return self._clip_text(unescape(re.sub(r"\s+", " ", match.group(1))).strip(), 160)

    def _extract_links_fallback(self, html: str, target_url: str) -> List[Dict[str, str]]:
        links: List[Dict[str, str]] = []
        seen = set()
        for match in re.finditer(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html):
            href = match.group(1).strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(target_url, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            text = re.sub(r"(?is)<[^>]+>", " ", match.group(2))
            text = self._clip_text(unescape(" ".join(text.split())), 180)
            links.append({"url": absolute, "text": text or "[no text]"})
            if len(links) >= 200:
                break
        return links

    def _extract_headings_fallback(self, html: str) -> List[Dict[str, Any]]:
        headings: List[Dict[str, Any]] = []
        for lvl in range(1, 7):
            pattern = rf"(?is)<h{lvl}[^>]*>(.*?)</h{lvl}>"
            for match in re.finditer(pattern, html):
                text = re.sub(r"(?is)<[^>]+>", " ", match.group(1))
                text = unescape(" ".join(text.split()))
                if text:
                    headings.append({"level": lvl, "text": self._clip_text(text, 180), "id": ""})
        return headings[:120]

    def _fetch_via_flaresolverr(self, url: str) -> Optional[str]:
        if not self.valves.FLARESOLVERR_URL:
            return None
        try:
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": self.valves.FLARESOLVERR_TIMEOUT * 1000,
            }
            resp = self.http.post_json(
                self.valves.FLARESOLVERR_URL,
                timeout=self.valves.FLARESOLVERR_TIMEOUT + 10,
                payload=payload,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") == "ok":
                return body.get("solution", {}).get("response", "")
        except Exception as exc:
            logger.warning("FlareSolverr failed for %s: %s", url, exc)
        return None

    def _extract_text(self, html: str) -> str:
        if self.runtime.html_parser_available() and _BeautifulSoup is not None:
            soup = _BeautifulSoup(html, "html.parser")
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
        else:
            normalized = self._simple_html_to_text(html)

        if len(normalized) > self.valves.MAX_PAGE_CONTENT_CHARS:
            return normalized[: self.valves.MAX_PAGE_CONTENT_CHARS] + "\n\n[Content truncated]"
        return normalized

    def _extract_structure_with_bs4(self, html: str, target_url: str) -> Dict[str, Any]:
        soup = _BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        out = {
            "meta": {},
            "headings": [],
            "links": [],
            "tables": [],
            "sections": [],
            "code_blocks": [],
            "lists": [],
        }

        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            out["meta"]["description"] = desc["content"]
        canonical = soup.find("link", attrs={"rel": "canonical"})
        if canonical and canonical.get("href"):
            out["meta"]["canonical_url"] = canonical["href"]

        for tag in soup.find_all(re.compile(r"^h[1-6]$")):
            t = tag.get_text(strip=True)
            if t:
                out["headings"].append({"level": int(tag.name[1]), "text": t, "id": tag.get("id", "")})

        seen_links = set()
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(target_url, href)
            if absolute in seen_links:
                continue
            seen_links.add(absolute)
            out["links"].append({"url": absolute, "text": tag.get_text(strip=True) or "[no text]"})
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
                out["tables"].append({"index": idx, "headers": headers, "rows": rows[:100], "records": records[:100]})
        out["tables"] = out["tables"][:20]

        for block in soup.find_all(["pre", "code"]):
            text = block.get_text(strip=True)
            if text and len(text) > 10:
                out["code_blocks"].append({"language": "", "content": text[:5000]})
        out["code_blocks"] = out["code_blocks"][:30]

        for lst in soup.find_all(["ul", "ol"]):
            items = [li.get_text(strip=True)[:500] for li in lst.find_all("li", recursive=False)]
            items = [i for i in items if i]
            if len(items) >= 2:
                out["lists"].append({"type": "ordered" if lst.name == "ol" else "unordered", "items": items[:50]})
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

        return out

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
            resp = self.http.get(url, timeout=self.valves.REQUEST_TIMEOUT)
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

            if self.runtime.html_parser_available() and _BeautifulSoup is not None:
                soup = _BeautifulSoup(html, "html.parser")
                title_tag = soup.find("title")
                result["title"] = title_tag.get_text(strip=True) if title_tag else urlparse(url).netloc
            else:
                result["title"] = self._extract_title_fallback(html, urlparse(url).netloc)
            result["content"] = self._extract_text(html)

        except RuntimeHttpError as exc:
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
                resp = self.http.get(target_url, timeout=self.valves.REQUEST_TIMEOUT)
                html = resp.text

            if self.runtime.html_parser_available() and _BeautifulSoup is not None:
                bs4_data = self._extract_structure_with_bs4(html, target_url)
                out.update(bs4_data)
            else:
                out["headings"] = self._extract_headings_fallback(html)
                out["links"] = self._extract_links_fallback(html, target_url)
                extracted = self._extract_text(html)
                section_lines = [line for line in extracted.splitlines() if line.strip()]
                if section_lines:
                    out["sections"].append({
                        "heading": "content",
                        "heading_level": 1,
                        "content": "\n".join(section_lines[:80]),
                    })
                out["meta"]["parser_mode"] = "fallback"

        except RuntimeHttpError as exc:
            out["error"] = str(exc)

        return out


# ---------------------------------------------------------------------------
# Ranking, evidence, and response building
# ---------------------------------------------------------------------------

class ResponseBuilderMixin:
    def _clip(self, text: str, limit: int = 240) -> str:
        if not text:
            return ""
        normalized = " ".join(str(text).split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."

    def _best_excerpt(self, text: str, query: str, limit: int = 320) -> str:
        if not text:
            return ""
        normalized = " ".join(text.split())
        terms = list(self._term_signature(query))[:6]
        if not terms:
            return self._clip(normalized, limit)
        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        best = ""
        best_score = -1
        for sentence in sentences:
            lower = sentence.lower()
            score = sum(1 for term in terms if term in lower)
            if score > best_score and len(sentence) >= 40:
                best_score = score
                best = sentence
        return self._clip(best or normalized, limit)

    def _build_citations(self, pages: List[Dict[str, Any]], query: str, limit: int = 10) -> List[Dict[str, Any]]:
        citations: List[Dict[str, Any]] = []
        for idx, page in enumerate(pages[:limit], start=1):
            url = page.get("url", "")
            parsed = urlparse(url) if url else None
            source = parsed.netloc.lower() if parsed else ""
            seed = re.sub(r"[^a-z0-9]+", "-", f"{source}{parsed.path if parsed else ''}".lower()).strip("-")
            passage_id = f"p{idx}-{(seed or 'source')[:48]}"
            raw_text = page.get("content") or page.get("snippet") or ""
            citations.append(
                {
                    "id": idx,
                    "title": page.get("title", "Untitled"),
                    "url": url,
                    "source": source,
                    "excerpt": self._best_excerpt(raw_text, query),
                    "published_at": "",
                    "relevance_score": round(float(page.get("quality_score", page.get("rrf_score", 0.0))), 3),
                    "passage_id": passage_id,
                }
            )
        return citations

    def _build_findings(self, citations: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        for citation in citations[:limit]:
            excerpt = citation.get("excerpt", "")
            if not excerpt:
                continue
            findings.append({"claim": excerpt, "citation_ids": [citation["id"]]})
        return findings

    def _build_sources(self, citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        seen = set()
        sources: List[Dict[str, str]] = []
        for citation in citations:
            url = citation.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "title": citation.get("title", "Untitled"),
                    "url": url,
                    "source": citation.get("source", ""),
                }
            )
        return sources

    def _build_follow_ups(self, query: str, pages: List[Dict[str, Any]], mode: str, limit: int = 3) -> List[str]:
        prompts: List[str] = []
        missing = self._missing_query_terms(query, pages)
        if missing:
            prompts.append(f"{query} {' '.join(missing[:3])}")
        if mode in {"fast", "fast_fallback"}:
            prompts.append(f"{query} official documentation")
        prompts.append(f"{query} limitations tradeoffs")
        prompts.append(f"{query} recent updates")

        deduped: List[str] = []
        seen = set()
        for item in prompts:
            key = item.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(item.strip())
        return deduped[:limit]

    def _derive_confidence(self, coverage: int, avg_quality: float, failures: List[str], citations: List[Dict[str, Any]]) -> str:
        if not citations:
            return "low"
        failure_ratio = len(failures) / max(1, len(citations))
        if coverage >= 4 and avg_quality >= 0.45 and failure_ratio <= 0.25:
            return "high"
        if coverage >= 2 and avg_quality >= 0.25:
            return "medium"
        return "low"

    def _build_direct_answer(self, mode: str, findings: List[Dict[str, Any]], confidence: str) -> str:
        if not findings:
            return ""
        lead = findings[0].get("claim", "")
        if not lead:
            return ""
        return self._clip(f"[{mode} | confidence={confidence}] {lead}", 320)

    def _build_structured_response(
        self,
        query: str,
        mode: str,
        pages: List[Dict[str, Any]],
        ranked: List[Dict[str, Any]],
        deep_synthesis: Optional[Dict[str, Any]],
        deep_fusion: Optional[Dict[str, Any]],
        reasoning: Dict[str, Any],
        query_plan: List[Dict[str, str]],
        failures: List[str],
        warnings: List[str],
        follow_up_queries: List[str],
        iterations: int,
        total_ms: int,
        compatibility: bool = True,
    ) -> Dict[str, Any]:
        avg_quality = reasoning.get("avg_quality", 0.0)
        coverage = int(reasoning.get("coverage", 0))
        citations = self._build_citations(pages, query)
        findings = self._build_findings(citations)
        sources = self._build_sources(citations)
        confidence = self._derive_confidence(coverage, float(avg_quality), failures, citations)
        direct_answer = self._build_direct_answer(mode, findings, confidence)
        summary = self._clip(f"Collected {len(citations)} citations across {len(sources)} sources in {mode} mode.", 180)

        response: Dict[str, Any] = {
            "query": query,
            "mode": mode,
            "direct_answer": direct_answer,
            "summary": summary,
            "findings": findings,
            "citations": citations,
            "sources": sources,
            "follow_up_queries": follow_up_queries,
            "diagnostics": {
                "warnings": warnings,
                "errors": failures,
                "runtime": self.runtime.diagnostics(),
                "query_plan": query_plan,
                "iterations": iterations,
                "coverage_notes": [
                    f"usable_pages={coverage}",
                    f"avg_quality={round(float(avg_quality), 3)}",
                ],
                "search_count": len(query_plan),
                "fetched_count": len(pages),
                "ranked_passage_count": len(citations),
            },
            "timings": {"total_ms": total_ms},
            "confidence": confidence,
        }

        if compatibility:
            response["legacy"] = {
                "results_scraped": pages,
                "results_ranked": ranked,
                "deep_synthesis": deep_synthesis,
                "deep_fusion": deep_fusion,
                "reasoning": reasoning,
                "notes": {
                    "compatibility": "Legacy fields retained temporarily under legacy.*",
                    "migration": "Read top-level findings/citations/sources/diagnostics instead of legacy keys.",
                },
            }

        return response


# ---------------------------------------------------------------------------
# Research orchestration
# ---------------------------------------------------------------------------

class Tools(ResponseBuilderMixin):
    Valves = Valves
    UserValves = UserValves

    def __init__(self):
        self.valves = self.Valves()
        self.runtime = RuntimeCompatibility(self.valves.STRICT_COMPAT_MODE)
        self.http = RuntimeHttpClient(
            use_requests=self.runtime.transport_backend == "requests",
            default_headers={
                "User-Agent": self.valves.USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
            },
        )

    def _cfg(self, key: str, user_valves: Optional[Any], default: Any = None) -> Any:
        if user_valves and hasattr(user_valves, key):
            value = getattr(user_valves, key)
            if value is not None:
                return value
        return getattr(self.valves, key, default)

    def _term_signature(self, text: str) -> set:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) >= 3 and token not in STOPWORDS
        }

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
        return urlunparse((parsed.scheme or "https", netloc, path, "", urlencode(clean_query, doseq=True), ""))

    def _ignored_domains(self) -> set:
        if not self.valves.IGNORED_DOMAINS.strip():
            return set()
        return {d.strip().lower() for d in self.valves.IGNORED_DOMAINS.split(",") if d.strip()}

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
            enriched = f"{info['date']} (today) -> {tmr.strftime('%Y-%m-%d')} {tmr.strftime('%A')} (tomorrow): {query}"
        elif "yesterday" in ql:
            yst = now - timedelta(days=1)
            enriched = f"{info['date']} (today) -> {yst.strftime('%Y-%m-%d')} {yst.strftime('%A')} (yesterday): {query}"
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
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(item.strip())
        return deduped[: self.valves.QUERY_VARIANTS_LIMIT]

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
        resp = self.http.get(url, params=params, timeout=self.valves.REQUEST_TIMEOUT)
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
        for normalized_url, score in ranked:
            doc = merged[normalized_url]
            doc["rrf_score"] = round(score, 6)
            fused.append(doc)
        return fused

    async def _vane_deep_search(self, query: str, source_mode: str, depth: str) -> Dict[str, Any]:
        if not self.valves.ENABLE_VANE_DEEP:
            return {"enabled": False, "error": "Vane deep search is disabled"}

        if not self.valves.VANE_CHAT_MODEL_PROVIDER_ID or not self.valves.VANE_EMBEDDING_MODEL_PROVIDER_ID:
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
            timeout = self.valves.VANE_TIMEOUT
            resp = None
            for attempt in range(2):
                try:
                    resp = self.http.post_json(
                        f"{self.valves.VANE_URL.rstrip('/')}/api/search",
                        payload=payload,
                        timeout=timeout,
                    )
                    break
                except RuntimeReadTimeout:
                    if attempt == 0:
                        timeout = min(timeout + 30, 180)
                        continue
                    raise
            if resp is None:
                return {"enabled": True, "error": "Vane request did not return a response", "sources": []}
            resp.raise_for_status()
            data = resp.json()
            sources = []
            for src in data.get("sources", []):
                meta = src.get("metadata", {})
                sources.append({"title": meta.get("title", "Untitled"), "url": meta.get("url", ""), "content": src.get("content", "")})
            return {"enabled": True, "message": data.get("message", ""), "sources": sources}
        except Exception as exc:
            return {"enabled": True, "error": str(exc), "sources": []}

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

    def _is_meaningful_vane_response(self, deep_synthesis: Optional[Dict[str, Any]]) -> bool:
        if not deep_synthesis or deep_synthesis.get("error"):
            return False
        message = (deep_synthesis.get("message") or "").strip()
        if len(message) < 120:
            return False
        lower = message.lower()
        if any(pattern in lower for pattern in LOW_CONFIDENCE_VANE_PATTERNS):
            return False
        if not deep_synthesis.get("sources") and len(set(re.findall(r"[a-zA-Z]{3,}", lower))) < 14:
            return False
        return True

    def _term_overlap(self, left: str, right: str) -> float:
        a = self._term_signature(left)
        b = self._term_signature(right)
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    def _fuse_deep_signals(self, fast_pages: List[Dict[str, Any]], deep_synthesis: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not deep_synthesis or deep_synthesis.get("error"):
            return {
                "enabled": False,
                "reason": "deep_synthesis_unavailable",
                "consensus": [],
                "fast_additions": [],
                "vane_additions": [],
            }

        if not self._is_meaningful_vane_response(deep_synthesis):
            return {
                "enabled": False,
                "reason": "deep_synthesis_low_confidence",
                "consensus": [],
                "fast_additions": self._fast_bullets(fast_pages)[:5],
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
                consensus.append({"vane": vb, "fast": fast[best_idx], "overlap": round(best_score, 3)})
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

    def _next_research_query(self, original_query: str, enriched_query: str, pages: List[Dict[str, Any]], iterations_used: int) -> str:
        if iterations_used == 1:
            return enriched_query
        return self._heuristic_followup_query(original_query, pages)

    def _should_continue_research(self, original_query: str, pages: List[Dict[str, Any]], cycle: int, max_iterations: int) -> bool:
        if cycle < self.valves.RESEARCH_MIN_ITERATIONS:
            return True

        coverage = self._evidence_coverage(original_query, pages)
        redundancy = self._evidence_redundancy(pages)
        if coverage >= 0.65 and redundancy >= 0.65:
            return False
        if coverage >= 0.8:
            return redundancy < 0.8 and len(pages) < max_iterations * 2

        return len(pages) < max(6, cycle * 2) or coverage < 0.5 or redundancy < 0.55

    def _compute_quality_metrics(self, pages: List[Dict[str, Any]]) -> Tuple[int, float]:
        coverage = sum(1 for item in pages if len(item.get("content", "")) >= self.valves.MIN_CONTENT_CHARS)
        avg_quality = sum(item.get("quality_score", 0.0) for item in pages) / len(pages) if pages else 0.0
        return coverage, avg_quality

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
        top_for_scrape = fused[: self.valves.PAGES_TO_SCRAPE]

        await emitter.status(f"Fused {sum(len(rs) for rs in result_sets)} raw results -> {len(fused)} unique URLs")

        scraper = PageScraper(self.valves, self.runtime)
        scraped: List[Dict[str, Any]] = []

        if self.runtime.use_threadpool():
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.valves.CONCURRENT_SCRAPE_WORKERS) as executor:
                futures = {executor.submit(scraper.scrape, item["url"]): item for item in top_for_scrape}
                for future in concurrent.futures.as_completed(futures):
                    scraped.append(self._compose_scraped_result(futures[future], future))
        else:
            for base in top_for_scrape:
                try:
                    page = scraper.scrape(base["url"])
                    scraped.append(self._compose_scraped_result_from_page(base, page))
                except Exception as exc:
                    scraped.append(self._compose_scraped_error(base, str(exc)))

        scraped = sorted(scraped, key=lambda x: (x.get("quality_score", 0.0), x.get("rrf_score", 0.0)), reverse=True)
        return fused, scraped, failures, variants

    def _compose_scraped_result(self, base: Dict[str, Any], future: concurrent.futures.Future) -> Dict[str, Any]:
        try:
            page = future.result()
            return self._compose_scraped_result_from_page(base, page)
        except Exception as exc:
            return self._compose_scraped_error(base, str(exc))

    def _compose_scraped_result_from_page(self, base: Dict[str, Any], page: Dict[str, Any]) -> Dict[str, Any]:
        text = page.get("content", "")
        if text and len(text) < self.valves.MIN_CONTENT_CHARS:
            text = ""
        score = self._source_quality_score(page.get("url", base["url"]), page.get("title", base["title"]), text)
        return {
            "url": page.get("url", base["url"]),
            "title": page.get("title", base["title"]),
            "snippet": base.get("snippet", ""),
            "content": text or base.get("snippet", ""),
            "source": page.get("source", "direct"),
            "error": page.get("error"),
            "rrf_score": base.get("rrf_score", 0.0),
            "quality_score": score,
        }

    def _compose_scraped_error(self, base: Dict[str, Any], error: str) -> Dict[str, Any]:
        return {
            "url": base.get("url", ""),
            "title": base.get("title", ""),
            "snippet": base.get("snippet", ""),
            "content": base.get("snippet", ""),
            "source": "failed",
            "error": error,
            "rrf_score": base.get("rrf_score", 0.0),
            "quality_score": 0.0,
        }

    async def _emit_reasoning(self, emitter: EventEmitter, requested_mode: str, selected_mode: str, variants: List[str], avg_quality: float, coverage: int):
        await emitter.message(
            "\n".join(
                [
                    "### Elevated Search v1.2 Reasoning",
                    f"- Mode requested: {requested_mode}",
                    f"- Mode selected: {selected_mode}",
                    f"- Query variants: {len(variants)}",
                    f"- Avg source quality: {round(avg_quality, 3)}",
                    f"- Coverage (usable pages): {coverage}",
                ]
            )
        )

    async def _run_research_mode(
        self,
        query: str,
        enriched_query: str,
        requested_mode: str,
        max_iterations: int,
        emitter: EventEmitter,
        include_citations: bool,
        show_reasoning: bool,
        mode_prefix_override: Optional[str],
        dt_info: Dict[str, str],
        runtime_warnings: List[str],
        started_at: float,
    ) -> Dict[str, Any]:
        all_pages: List[Dict[str, Any]] = []
        all_ranked: List[Dict[str, Any]] = []
        queries_used: List[str] = []
        seen_urls = set()
        search_failures: List[str] = []

        cycle = 0
        while cycle < max_iterations:
            cycle += 1
            cycle_query = self._next_research_query(query, enriched_query, all_pages, cycle)
            if cycle_query in queries_used and cycle > 1:
                break
            queries_used.append(cycle_query)

            await emitter.status(f"Research cycle {cycle}/{max_iterations}: {cycle_query[:120]}")

            ranked, scraped, failures, _variants = await self._search_and_scrape(cycle_query, emitter)
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
            await emitter.status(f"Cycle {cycle}: +{len(new_pages)} pages, total {len(all_pages)}")

            if not new_pages and cycle >= self.valves.RESEARCH_MIN_ITERATIONS:
                break

            if not self._should_continue_research(query, all_pages, cycle, max_iterations):
                await emitter.status(f"Research stopping after {cycle} cycles")
                break

        if include_citations:
            for page in all_pages[:10]:
                if page.get("url") and page.get("content"):
                    await emitter.citation(page.get("title", "Untitled"), page["url"], page["content"])

        coverage, avg_quality = self._compute_quality_metrics(all_pages)
        reasoning = {
            "mode_requested": requested_mode,
            "mode_selected": "research",
            "mode_prefix_override": mode_prefix_override,
            "is_temporal_query": self._is_temporal_query(query),
            "query_variants": queries_used,
            "avg_quality": round(avg_quality, 3),
            "coverage": coverage,
            "search_failures": search_failures,
            "datetime_context": dt_info,
            "rrf_k": self.valves.RRF_K,
        }

        if show_reasoning:
            await emitter.message(
                "\n".join(
                    [
                        "### Elevated Search v1.2 Reasoning",
                        f"- Mode requested: {requested_mode}",
                        "- Mode selected: research",
                        f"- Iterations used: {cycle}",
                        f"- Sources gathered: {len(all_pages)}",
                    ]
                )
            )

        total_ms = int((time.perf_counter() - started_at) * 1000)
        query_plan = [{"text": item, "purpose": "research-cycle"} for item in queries_used]

        return self._build_structured_response(
            query=query,
            mode="research",
            pages=all_pages,
            ranked=all_ranked,
            deep_synthesis=None,
            deep_fusion=None,
            reasoning=reasoning,
            query_plan=query_plan,
            failures=search_failures,
            warnings=list(runtime_warnings),
            follow_up_queries=self._build_follow_ups(query, all_pages, "research"),
            iterations=cycle,
            total_ms=total_ms,
        )

    async def _run_primary_modes(
        self,
        query: str,
        enriched_query: str,
        requested_mode: str,
        source_mode: str,
        depth: str,
        emitter: EventEmitter,
        include_citations: bool,
        show_reasoning: bool,
        mode_prefix_override: Optional[str],
        dt_info: Dict[str, str],
        runtime_warnings: List[str],
        started_at: float,
        show_status: bool,
    ) -> Dict[str, Any]:
        deep_synthesis = None
        deep_fusion = None
        warnings: List[str] = list(runtime_warnings)

        if requested_mode == "deep":
            if show_status:
                await emitter.status("Deep mode: querying Vane first")
            deep_synthesis = await self._vane_deep_search(enriched_query, source_mode, depth)
            if show_status:
                await emitter.status("Deep mode: enriching with SearXNG evidence")

        fused, scraped, failures, variants = await self._search_and_scrape(enriched_query, emitter)

        if not fused:
            if deep_synthesis and deep_synthesis.get("error"):
                warnings.append("Deep synthesis failed while search returned no fused results")
            total_ms = int((time.perf_counter() - started_at) * 1000)
            no_results_reasoning = {
                "mode_requested": requested_mode,
                "mode_selected": requested_mode,
                "mode_prefix_override": mode_prefix_override,
                "is_complex_query": self._is_complex_query(query),
                "is_temporal_query": self._is_temporal_query(query),
                "query_variants": variants,
                "avg_quality": 0.0,
                "coverage": 0,
                "search_failures": failures,
                "datetime_context": dt_info,
                "rrf_k": self.valves.RRF_K,
            }
            return self._build_structured_response(
                query=query,
                mode=requested_mode,
                pages=[],
                ranked=[],
                deep_synthesis=deep_synthesis,
                deep_fusion=None,
                reasoning=no_results_reasoning,
                query_plan=[{"text": variant, "purpose": "primary" if idx == 0 else "expansion"} for idx, variant in enumerate(variants)],
                failures=failures + ["No search results returned from SearXNG across all query variants"],
                warnings=warnings,
                follow_up_queries=self._build_follow_ups(query, scraped, requested_mode),
                iterations=1,
                total_ms=total_ms,
            )

        coverage, avg_quality = self._compute_quality_metrics(scraped)
        selected_mode = requested_mode
        if requested_mode == "auto":
            selected_mode = self._resolve_auto_mode(query, avg_quality, coverage)

        if selected_mode == "deep":
            if deep_synthesis is None:
                if show_status:
                    await emitter.status("Escalating to deep synthesis via Vane")
                deep_synthesis = await self._vane_deep_search(enriched_query, source_mode, depth)
            deep_fusion = self._fuse_deep_signals(scraped, deep_synthesis)
            if deep_synthesis and (deep_synthesis.get("error") or not self._is_meaningful_vane_response(deep_synthesis)):
                selected_mode = "fast_fallback"
                warnings.append("Vane deep synthesis was weak or failed; used fast evidence fallback")
                if show_status:
                    await emitter.status("Vane deep synthesis was weak or failed; returning fast pipeline evidence")

        if include_citations:
            for item in scraped[:8]:
                if item.get("url") and item.get("content"):
                    await emitter.citation(item.get("title", "Untitled"), item["url"], item["content"])

        reasoning = {
            "mode_requested": requested_mode,
            "mode_selected": selected_mode,
            "mode_prefix_override": mode_prefix_override,
            "is_complex_query": self._is_complex_query(query),
            "is_temporal_query": self._is_temporal_query(query),
            "query_variants": variants,
            "avg_quality": round(avg_quality, 3),
            "coverage": coverage,
            "search_failures": failures,
            "datetime_context": dt_info,
            "rrf_k": self.valves.RRF_K,
        }

        if show_reasoning:
            await self._emit_reasoning(emitter, requested_mode, selected_mode, variants, avg_quality, coverage)

        total_ms = int((time.perf_counter() - started_at) * 1000)
        return self._build_structured_response(
            query=query,
            mode=selected_mode,
            pages=scraped,
            ranked=fused,
            deep_synthesis=deep_synthesis,
            deep_fusion=deep_fusion,
            reasoning=reasoning,
            query_plan=[{"text": variant, "purpose": "primary" if idx == 0 else "expansion"} for idx, variant in enumerate(variants)],
            failures=failures,
            warnings=warnings,
            follow_up_queries=self._build_follow_ups(query, scraped, selected_mode),
            iterations=1,
            total_ms=total_ms,
        )

    async def _run_research(
        self,
        query: str,
        requested_mode: str,
        source_mode: str,
        depth: str,
        max_iterations: int,
        emitter: EventEmitter,
        show_status: bool,
        include_citations: bool,
        show_reasoning: bool,
        mode_prefix_override: Optional[str],
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        enriched_query, dt_info = self._inject_temporal_context(query)
        runtime_warnings = self.runtime.diagnostics().get("degraded_reasons", [])

        if show_status:
            await emitter.status(f"Starting elevated search v1.2: {query}")

        if requested_mode == "research":
            if show_status:
                await emitter.status(f"Research mode: up to {max_iterations} cycles")
            response = await self._run_research_mode(
                query=query,
                enriched_query=enriched_query,
                requested_mode=requested_mode,
                max_iterations=max_iterations,
                emitter=emitter,
                include_citations=include_citations,
                show_reasoning=show_reasoning,
                mode_prefix_override=mode_prefix_override,
                dt_info=dt_info,
                runtime_warnings=runtime_warnings,
                started_at=started_at,
            )
            if show_status:
                total_sources = len(response.get("legacy", {}).get("results_scraped", []))
                iterations = response.get("diagnostics", {}).get("iterations", 0)
                await emitter.status(f"Complete: mode=research, sources={total_sources}, cycles={iterations}", status="complete", done=True)
            return response

        response = await self._run_primary_modes(
            query=query,
            enriched_query=enriched_query,
            requested_mode=requested_mode,
            source_mode=source_mode,
            depth=depth,
            emitter=emitter,
            include_citations=include_citations,
            show_reasoning=show_reasoning,
            mode_prefix_override=mode_prefix_override,
            dt_info=dt_info,
            runtime_warnings=runtime_warnings,
            started_at=started_at,
            show_status=show_status,
        )

        if show_status:
            mode_selected = response.get("mode", requested_mode)
            fused_count = len(response.get("legacy", {}).get("results_ranked", []))
            scraped_count = len(response.get("legacy", {}).get("results_scraped", []))
            await emitter.status(f"Complete: mode={mode_selected}, fused={fused_count}, scraped={scraped_count}", status="complete", done=True)

        return response

    # -----------------------------------------------------------------------
    # Open WebUI thin entrypoints
    # -----------------------------------------------------------------------

    async def elevated_search(
        self,
        query: str,
        mode: str = "auto",
        source_mode: str = "web",
        depth: str = "balanced",
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)

        user_valves = __user__.get("valves") if __user__ and __user__.get("valves") else None
        show_status = self._cfg("show_status_updates", user_valves, True)
        include_citations = self._cfg("include_citations", user_valves, True)
        show_reasoning = self._cfg("show_reasoning", user_valves, True)
        requested_mode = mode or self._cfg("mode", user_valves, "auto")
        max_iterations = self._cfg("max_iterations", user_valves, 5)
        query, requested_mode, mode_prefix_override = self._apply_mode_prefix(query, requested_mode)

        response = await self._run_research(
            query=query,
            requested_mode=requested_mode,
            source_mode=source_mode,
            depth=depth,
            max_iterations=max_iterations,
            emitter=emitter,
            show_status=show_status,
            include_citations=include_citations,
            show_reasoning=show_reasoning,
            mode_prefix_override=mode_prefix_override,
        )
        return json.dumps(response, ensure_ascii=False)

    async def fetch_page(
        self,
        url: str,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        if __user__:
            _ = __user__
        await emitter.status(f"Fetching page: {url}")

        scraper = PageScraper(self.valves, self.runtime)
        if self.runtime.use_threadpool():
            loop = asyncio.get_event_loop()
            page = await loop.run_in_executor(None, scraper.scrape, url)
        else:
            page = scraper.scrape(url)

        await emitter.status("Fetch complete", status="complete", done=True)
        return json.dumps(page, ensure_ascii=False)

    async def extract_page_structure(
        self,
        url: str,
        components: str = "all",
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        emitter = EventEmitter(__event_emitter__)
        if __user__:
            _ = __user__

        await emitter.status(f"Extracting structure: {url}")
        scraper = PageScraper(self.valves, self.runtime)
        if self.runtime.use_threadpool():
            loop = asyncio.get_event_loop()
            structure = await loop.run_in_executor(None, scraper.extract_structure, url)
        else:
            structure = scraper.extract_structure(url)

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
            requested = {c.strip().lower() for c in components.split(",") if c.strip()} & all_components

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
