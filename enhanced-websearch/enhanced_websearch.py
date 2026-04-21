"""
title: Enhanced Websearch Tool (Thin Client)
author: GitHub Copilot
version: 2.1.0
license: MIT
description: >
    Thin Open WebUI wrapper for the standalone Enhanced Websearch Service.
    Use concise_search for /search and research_search for /research.
    All heavy research logic runs in the backend service.
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
        default_factory=lambda: os.getenv("EWS_SERVICE_BASE_URL", "http://enhanced-websearch:8091"),
        description="Base URL for Enhanced Websearch Service",
    )
    BEARER_TOKEN: str = Field(
        default_factory=lambda: os.getenv("EWS_BEARER_TOKEN", ""),
        description="Optional bearer token for the backend service",
    )
    REQUEST_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("EWS_REQUEST_TIMEOUT", "60")),
        ge=3,
        le=120,
        description="HTTP timeout in seconds for backend requests",
    )


class UserValves(BaseModel):
    show_status_updates: bool = Field(default=True)
    max_iterations: int = Field(default=4, ge=1, le=8)
    search_max_results: int = Field(default=10, ge=1, le=20)


class Tools:
    Valves = Valves
    UserValves = UserValves

    def __init__(self):
        self.valves = self.Valves()

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
        headers = {"Content-Type": "application/json"}
        if self.valves.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {self.valves.BEARER_TOKEN}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _service_error(self, url: str, exc: Exception) -> dict:
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return {
                "error": f"Service HTTP {exc.code}",
                "details": body,
                "service_url": url,
            }
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", exc)
            reason_text = str(reason)
            if "timed out" in reason_text.lower():
                return {
                    "error": f"Service unavailable: backend request timed out after {self.valves.REQUEST_TIMEOUT}s",
                    "service_url": url,
                }
            return {
                "error": f"Service unavailable: {reason_text}",
                "service_url": url,
            }
        if isinstance(exc, TimeoutError):
            return {
                "error": f"Service unavailable: backend request timed out after {self.valves.REQUEST_TIMEOUT}s",
                "service_url": url,
            }
        return {
            "error": f"Service unavailable: {exc}",
            "service_url": url,
        }

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

    def _recency_cutoff(self, search_recency_filter: str, search_recency_amount: int) -> str:
        now = datetime.now(timezone.utc)
        if search_recency_filter == "hour":
            cutoff = now - timedelta(hours=search_recency_amount)
        elif search_recency_filter == "day":
            cutoff = now - timedelta(days=search_recency_amount)
        elif search_recency_filter == "week":
            cutoff = now - timedelta(days=7 * search_recency_amount)
        elif search_recency_filter == "month":
            cutoff = now - timedelta(days=31 * search_recency_amount)
        else:
            cutoff = now - timedelta(days=366 * search_recency_amount)
        return cutoff.isoformat()

    def _normalize_search_payload(
        self,
        query: str,
        max_results: int,
        display_server_time: bool,
        search_mode: str,
        search_recency_filter: str,
        search_recency_amount: int,
        country: Optional[str],
    ) -> dict:
        normalized_recency_filter = None if search_recency_filter == "none" else search_recency_filter
        normalized_search_mode = None if search_mode == "auto" else search_mode
        effective_recency_filter = normalized_recency_filter
        search_after_date_filter = None

        if normalized_recency_filter and search_recency_amount > 1:
            search_after_date_filter = self._recency_cutoff(normalized_recency_filter, search_recency_amount)
            effective_recency_filter = None

        return {
            "query": query,
            "max_results": max_results,
            "display_server_time": display_server_time,
            "search_mode": normalized_search_mode,
            "search_recency_filter": effective_recency_filter,
            "search_after_date_filter": search_after_date_filter,
            "country": country,
            "client": "open_webui",
        }

    def _post_json_stream(self, path: str, payload: dict) -> list[dict]:
        url = f"{self.valves.SERVICE_BASE_URL.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = self._build_headers({"Accept": "text/event-stream"})
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        event_type = "message"
        event_data: list[str] = []
        events: list[dict] = []

        def flush_event() -> None:
            nonlocal event_type, event_data
            if not event_data:
                event_type = "message"
                return
            raw = "\n".join(event_data)
            events.append({"event": event_type, "data": json.loads(raw)})
            event_type = "message"
            event_data = []

        try:
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        flush_event()
                        continue
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        event_data.append(line.split(":", 1)[1].strip())
                flush_event()
                return events
        except Exception as exc:
            return [{"event": "error", "data": self._service_error(url, exc)}]

    async def _post_json_with_progress(
        self,
        path: str,
        payload: dict,
        event_emitter: Optional[Any],
        show_status: bool,
        start_description: str,
        progress_description: str,
        done_description: str,
        stream_progress: bool = False,
    ) -> dict:
        if show_status:
            await self._emit_status(event_emitter, start_description)

        if stream_progress:
            events = await asyncio.to_thread(self._post_json_stream, f"{path}?stream=true", payload)
            emitted_states: set[str] = set()
            progress_messages = {
                "search_started": "Research started",
                "evidence_gathering": "Gathering evidence",
                "synthesizing": "Synthesizing answer",
                "complete": done_description,
                "error": "Research failed",
            }
            result_payload: Optional[dict] = None

            for event in events:
                data = event.get("data") or {}
                if event.get("event") == "result":
                    result_payload = data
                    continue
                state = data.get("state")
                if show_status and state and state not in emitted_states:
                    emitted_states.add(state)
                    message = data.get("message") or progress_messages.get(state, progress_description)
                    await self._emit_status(event_emitter, message, done=state == "complete")

            if result_payload is not None:
                if show_status and "complete" not in emitted_states and "error" not in result_payload:
                    await self._emit_status(event_emitter, done_description, done=True)
                return result_payload

            error_event = next((item for item in events if item.get("event") == "error"), None)
            if error_event:
                return error_event.get("data") or {"error": "Service unavailable"}
            return {"error": "Service unavailable: research stream ended without a result"}

        task = asyncio.create_task(asyncio.to_thread(self._post_json, path, payload))
        while not task.done():
            await asyncio.sleep(10)
            if task.done() or not show_status:
                break
            await self._emit_status(event_emitter, progress_description)

        response = await task

        if show_status and "error" not in response:
            await self._emit_status(event_emitter, done_description, done=True)

        return response

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
        """Perplexity-compatible concise search via /search."""
        user_valves = self._resolve_user_valves(__user__)
        effective_max_results = max_results or self._get_user_valve(user_valves, "search_max_results", 10)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)

        if search_recency_amount < 1:
            search_recency_amount = 1

        payload = self._normalize_search_payload(
            query=query,
            max_results=effective_max_results,
            display_server_time=display_server_time,
            search_mode=search_mode,
            search_recency_filter=search_recency_filter,
            search_recency_amount=search_recency_amount,
            country=country,
        )

        return await self._post_json_with_progress(
            "/search",
            payload,
            __event_emitter__,
            show_status,
            (
                "Calling concise search backend: "
                f"max_results={effective_max_results}, mode={search_mode}, "
                f"recency={search_recency_filter}, amount={search_recency_amount}"
            ),
            "Concise search still working...",
            "Concise search complete",
        )

    async def research_search(
        self,
        query: str,
        source_mode: str = "web",
        depth: str = "quality",
        max_iterations: Optional[int] = None,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        """Long-form structured research via /research."""
        user_valves = self._resolve_user_valves(__user__)
        effective_iterations = max_iterations or self._get_user_valve(user_valves, "max_iterations", 4)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)

        payload = {
            "query": query,
            "source_mode": source_mode,
            "depth": depth,
            "max_iterations": effective_iterations,
            "include_debug": False,
            "include_legacy": False,
            "strict_runtime": False,
            "user_context": {"client": "open_webui", "tool": "research"},
        }

        return await self._post_json_with_progress(
            "/research",
            payload,
            __event_emitter__,
            show_status,
            f"Calling long-form research backend: depth={depth}",
            "Long-form research still working...",
            "Long-form research complete",
            stream_progress=True,
        )

    async def fetch_page(
        self,
        url: str,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        if __user__:
            _ = __user__
        return await self._post_json_with_progress(
            "/fetch",
            {"url": url},
            __event_emitter__,
            True,
            f"Fetching via backend: {url}",
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
        if __user__:
            _ = __user__
        return await self._post_json_with_progress(
            "/extract",
            {"url": url, "components": components},
            __event_emitter__,
            True,
            f"Extracting via backend: {url}",
            "Extraction still working...",
            "Extraction complete",
        )
