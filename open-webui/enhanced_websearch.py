"""
title: Enhanced Websearch Tool
author: Binu
version: 3.0.0
license: MIT
description: >
    Open WebUI tool wrapper for the Enhanced Websearch Service.
    Provides concise_search for /search and research_search for /research,
    plus fetch_page and extract_page_structure helpers.
    All heavy logic runs in the backend service.
requirements: pydantic
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Valves(BaseModel):
    SERVICE_BASE_URL: str = Field(
        default_factory=lambda: os.getenv("EWS_SERVICE_BASE_URL", "http://localhost:8091"),
        description="Base URL for the Enhanced Websearch Service.",
    )
    BEARER_TOKEN: str = Field(
        default_factory=lambda: os.getenv("EWS_BEARER_TOKEN", ""),
        description="Optional bearer token for the backend service. Leave empty if not required.",
    )
    REQUEST_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("EWS_REQUEST_TIMEOUT", "120")),
        ge=3,
        le=600,
        description="HTTP timeout in seconds for backend requests. Increase for deep research.",
    )


class UserValves(BaseModel):
    show_status_updates: bool = Field(default=True)
    search_max_results: int = Field(default=10, ge=1, le=20)


class Tools:
    Valves = Valves
    UserValves = UserValves

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _emit_status(self, event_emitter: Optional[Any], description: str, done: bool = False):
        if not event_emitter:
            return
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "status": "complete" if done else "in_progress",
                    "description": description,
                    "done": done,
                },
            }
        )

    def _resolve_user_valves(self, user: Optional[dict]) -> Any:
        if not user:
            return None
        if isinstance(user, dict):
            return user.get("valves")
        return None

    def _get_user_valve(self, user_valves: Any, key: str, default: Any) -> Any:
        if user_valves is None:
            return default
        if isinstance(user_valves, dict):
            value = user_valves.get(key)
            return default if value is None else value
        value = getattr(user_valves, key, None)
        return default if value is None else value

    def _build_headers(self, extra_headers: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.valves.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {self.valves.BEARER_TOKEN}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _service_error(self, url: str, exc: Exception) -> dict:
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return {"error": f"Service HTTP {exc.code}", "details": body, "service_url": url}
        if isinstance(exc, urllib.error.URLError):
            reason = str(getattr(exc, "reason", exc))
            if "timed out" in reason.lower():
                return {"error": f"Backend request timed out after {self.valves.REQUEST_TIMEOUT}s", "service_url": url}
            return {"error": f"Service unavailable: {reason}", "service_url": url}
        if isinstance(exc, TimeoutError):
            return {"error": f"Backend request timed out after {self.valves.REQUEST_TIMEOUT}s", "service_url": url}
        return {"error": f"Service unavailable: {exc}", "service_url": url}

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.valves.SERVICE_BASE_URL.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._build_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return json.loads(text)
        except Exception as exc:
            return self._service_error(url, exc)

    # ------------------------------------------------------------------ #
    # Stream parser for /research
    # ------------------------------------------------------------------ #
    # The backend /research endpoint returns text/event-stream content type.
    # Current practice: lines are JSON objects with "type" and "data" fields,
    # e.g. {"type":"response","data":"some text here"}
    #
    # We consume all events and extract the accumulated body text for the
    # model to synthesize. We also return the raw events list for debugging.
    # ------------------------------------------------------------------ #

    def _post_research_stream(self, path: str, payload: dict) -> dict:
        """POST to /research, consume the SSE/JSON-lines stream, return
        the accumulated body text plus metadata."""
        url = f"{self.valves.SERVICE_BASE_URL.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = self._build_headers({"Accept": "text/event-stream"})
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        body_parts: list[str] = []
        event_type = "message"
        data_lines: list[str] = []
        events: list[dict] = []
        error_info: Optional[dict] = None

        def _flush_sse_block() -> None:
            nonlocal event_type, data_lines
            if not data_lines:
                event_type = "message"
                return
            raw = "\n".join(data_lines)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            events.append({"event": event_type, "data": parsed})
            data_lines = []
            event_type = "message"

        def _handle_line(line: str) -> None:
            nonlocal event_type
            if not line:
                _flush_sse_block()
                return
            # Try JSON-line first (the common case)
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    events.append({"event": "message", "data": obj})
                    # Extract body text from response events
                    if obj.get("type") == "response":
                        d = obj.get("data", "")
                        if isinstance(d, str) and d:
                            body_parts.append(d)
                    return
                except json.JSONDecodeError:
                    pass
            # Fall back to SSE-style framing
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

        try:
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                for raw_line in resp:
                    _handle_line(raw_line.decode("utf-8", errors="replace").strip())
                _flush_sse_block()
        except Exception as exc:
            error_info = self._service_error(url, exc)

        full_body = "".join(body_parts) if body_parts else None

        return {
            "body": full_body,
            "events": events,
            "event_count": len(events),
            "error": error_info,
        }

    # ------------------------------------------------------------------ #
    # Progress wrapper
    # ------------------------------------------------------------------ #

    async def _post_with_progress(
        self,
        path: str,
        payload: dict,
        event_emitter: Optional[Any],
        show_status: bool,
        start_description: str,
        progress_description: str,
        done_description: str,
        stream: bool = False,
    ) -> dict:
        if show_status:
            await self._emit_status(event_emitter, start_description)

        task = asyncio.create_task(
            asyncio.to_thread(self._post_research_stream if stream else self._post_json, path, payload)
        )

        while not task.done():
            await asyncio.sleep(8)
            if task.done() or not show_status:
                break
            await self._emit_status(event_emitter, progress_description)

        result = await task

        if show_status:
            if result.get("error"):
                error = result["error"]
                message = error.get("error", "Request failed") if isinstance(error, dict) else str(error)
                await self._emit_status(event_emitter, f"Error: {message}", done=True)
            else:
                await self._emit_status(event_emitter, done_description, done=True)

        return result

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    async def concise_search(
        self,
        query: str,
        max_results: Optional[int] = None,
        display_server_time: bool = False,
        search_mode: Literal["auto", "web", "academic", "sec"] = "auto",
        search_recency_filter: Literal["none", "hour", "day", "week", "month", "year"] = "none",
        search_recency_amount: int = 1,
        country: Optional[str] = None,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Concise web search via the local /search endpoint.
        Best for quick factual lookups and lightweight comparisons."""
        user_valves = self._resolve_user_valves(__user__)
        effective_max_results = max_results or self._get_user_valve(user_valves, "search_max_results", 10)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)

        if search_recency_amount < 1:
            search_recency_amount = 1

        # Normalize: "auto" → None (backend treats it as default behavior)
        normalized_search_mode = None if search_mode == "auto" else search_mode

        # Recency handling
        normalized_recency_filter = None if search_recency_filter == "none" else search_recency_filter
        search_after_date_filter = None
        effective_recency_filter = normalized_recency_filter

        if normalized_recency_filter and search_recency_amount > 1:
            now = datetime.now(timezone.utc)
            if normalized_recency_filter == "hour":
                cutoff = now - timedelta(hours=search_recency_amount)
            elif normalized_recency_filter == "day":
                cutoff = now - timedelta(days=search_recency_amount)
            elif normalized_recency_filter == "week":
                cutoff = now - timedelta(days=7 * search_recency_amount)
            elif normalized_recency_filter == "month":
                cutoff = now - timedelta(days=31 * search_recency_amount)
            else:  # year
                cutoff = now - timedelta(days=366 * search_recency_amount)
            search_after_date_filter = cutoff.isoformat()
            effective_recency_filter = None

        payload = {
            "query": query,
            "max_results": effective_max_results,
            "display_server_time": display_server_time,
            "search_mode": normalized_search_mode,
            "search_recency_filter": effective_recency_filter,
            "search_after_date_filter": search_after_date_filter,
            "country": country,
            "client": "open_webui",
        }

        return await self._post_with_progress(
            "/search",
            payload,
            __event_emitter__,
            show_status,
            f"Searching: {query[:80]}{'...' if len(query) > 80 else ''}",
            "Search still working...",
            "Search complete",
        )

    async def research_search(
        self,
        query: str,
        source_mode: Literal["web", "academia", "social", "all"] = "web",
        depth: Literal["speed", "balanced", "quality"] = "balanced",
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Long-form research via the local /research endpoint.
        Proxies a Vane-backed streaming research run.
        Best for broad, technical, evaluative, or source-sensitive questions.

        depth options:
        - speed: fastest lightweight pass
        - balanced: recommended default
        - quality: heavier/slower pass for depth
        """
        user_valves = self._resolve_user_valves(__user__)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)

        # Current /research contract:
        # query, source_mode, depth, optional history, optional system_instructions, user_context
        # No max_iterations, include_debug, include_legacy, strict_runtime.
        payload = {
            "query": query,
            "source_mode": source_mode,
            "depth": depth,
            "history": [],
            "system_instructions": "",
            "user_context": {"client": "open_webui", "tool": "research"},
        }

        result = await self._post_with_progress(
            "/research",
            payload,
            __event_emitter__,
            show_status,
            f"Researching: {query[:80]}{'...' if len(query) > 80 else ''} (depth={depth})",
            "Research still working...",
            "Research complete",
            stream=True,
        )

        # If the backend returned a body, expose it as the primary content
        body = result.get("body")
        if body:
            result["content"] = body

        return result

    async def fetch_page(
        self,
        url: str,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Fetch and extract text content from a single URL."""
        user_valves = self._resolve_user_valves(__user__)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)
        return await self._post_with_progress(
            "/fetch",
            {"url": url},
            __event_emitter__,
            show_status,
            f"Fetching: {url}",
            "Fetch still working...",
            "Fetch complete",
        )

    async def extract_page_structure(
        self,
        url: str,
        components: str = "all",
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Extract structured metadata and components from a single URL."""
        user_valves = self._resolve_user_valves(__user__)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)
        return await self._post_with_progress(
            "/extract",
            {"url": url, "components": components},
            __event_emitter__,
            show_status,
            f"Extracting: {url}",
            "Extraction still working...",
            "Extraction complete",
        )
