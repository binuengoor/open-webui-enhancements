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

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.valves.SERVICE_BASE_URL.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.valves.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {self.valves.BEARER_TOKEN}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return json.loads(text)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return {
                "error": f"Service HTTP {exc.code}",
                "details": body,
                "service_url": url,
            }
        except urllib.error.URLError as exc:
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
        except TimeoutError:
            return {
                "error": f"Service unavailable: backend request timed out after {self.valves.REQUEST_TIMEOUT}s",
                "service_url": url,
            }
        except Exception as exc:
            return {
                "error": f"Service unavailable: {exc}",
                "service_url": url,
            }

    async def _post_json_with_progress(
        self,
        path: str,
        payload: dict,
        event_emitter: Optional[Any],
        show_status: bool,
        start_description: str,
        progress_description: str,
        done_description: str,
    ) -> dict:
        if show_status:
            await self._emit_status(event_emitter, start_description)

        task = asyncio.create_task(asyncio.to_thread(self._post_json, path, payload))
        while not task.done():
            await asyncio.sleep(10)
            if task.done() or not show_status:
                break
            await self._emit_status(event_emitter, progress_description)

        response = await task

        if show_status:
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
    ) -> str:
        """Perplexity-compatible concise search via /search."""
        user_valves = self._resolve_user_valves(__user__)
        effective_max_results = max_results or self._get_user_valve(user_valves, "search_max_results", 10)
        show_status = self._get_user_valve(user_valves, "show_status_updates", True)

        if search_recency_amount < 1:
            search_recency_amount = 1

        payload = {
            "query": query,
            "max_results": effective_max_results,
            "display_server_time": display_server_time,
            "search_mode": None if search_mode == "auto" else search_mode,
            "search_recency_filter": None if search_recency_filter == "none" else search_recency_filter,
            "search_recency_amount": search_recency_amount,
            "country": country,
        }

        response = await self._post_json_with_progress(
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

        return json.dumps(response, ensure_ascii=False)

    async def research_search(
        self,
        query: str,
        source_mode: str = "web",
        depth: str = "quality",
        max_iterations: Optional[int] = None,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
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
            "user_context": {},
        }

        response = await self._post_json_with_progress(
            "/research",
            payload,
            __event_emitter__,
            show_status,
            f"Calling long-form research backend: depth={depth}",
            "Long-form research still working...",
            "Long-form research complete",
        )

        return json.dumps(response, ensure_ascii=False)

    async def fetch_page(
        self,
        url: str,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        if __user__:
            _ = __user__
        response = await self._post_json_with_progress(
            "/fetch",
            {"url": url},
            __event_emitter__,
            True,
            f"Fetching via backend: {url}",
            "Fetch still working...",
            "Fetch complete",
        )
        return json.dumps(response, ensure_ascii=False)

    async def extract_page_structure(
        self,
        url: str,
        components: str = "all",
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        if __user__:
            _ = __user__
        response = await self._post_json_with_progress(
            "/extract",
            {"url": url, "components": components},
            __event_emitter__,
            True,
            f"Extracting via backend: {url}",
            "Extraction still working...",
            "Extraction complete",
        )
        return json.dumps(response, ensure_ascii=False)
