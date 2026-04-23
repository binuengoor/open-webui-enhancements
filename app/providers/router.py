from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, List, Tuple

from app.models.internal import ProviderHealthRecord
from app.providers.base import ProviderError, SearchProvider


logger = logging.getLogger(__name__)


@dataclass
class ProviderSlot:
    provider: SearchProvider
    weight: int
    enabled: bool


class ProviderRouter:
    def __init__(self, slots: List[ProviderSlot], cooldown_seconds: int, failure_threshold: int):
        self._slots = slots
        self._cooldown_seconds = cooldown_seconds
        self._failure_threshold = max(1, failure_threshold)
        self._cursor = 0
        self._lock = Lock()
        self._health: Dict[str, ProviderHealthRecord] = {
            slot.provider.name: ProviderHealthRecord(name=slot.provider.name, enabled=slot.enabled)
            for slot in slots
        }

    def health_snapshot(self) -> List[ProviderHealthRecord]:
        return list(self._health.values())

    def get_provider(self, name: str) -> SearchProvider | None:
        for slot in self._slots:
            if slot.provider.name == name:
                return slot.provider
        return None

    def _is_ready(self, rec: ProviderHealthRecord) -> bool:
        return rec.enabled and rec.cooldown_until <= time.time()

    def _pick_order(self, mode: str | None = None) -> List[ProviderSlot]:
        with self._lock:
            weighted: List[ProviderSlot] = []
            for slot in self._slots:
                for _ in range(max(1, slot.weight)):
                    weighted.append(slot)
            if not weighted:
                return []
            pivot = self._cursor % len(weighted)
            self._cursor += 1
            rotated = weighted[pivot:] + weighted[:pivot]

            unique_order: List[ProviderSlot] = []
            seen: set[str] = set()
            for slot in rotated:
                name = slot.provider.name
                if name in seen:
                    continue
                seen.add(name)
                unique_order.append(slot)

            return unique_order

    def _mark_success(self, name: str) -> None:
        rec = self._health[name]
        rec.consecutive_failures = 0
        rec.consecutive_empty_results = 0
        rec.last_success_at = time.time()
        rec.last_failure_reason = ""
        rec.last_failure_type = ""
        rec.last_cooldown_seconds = 0
        rec.cooldown_until = 0.0

    def _cooldown_seconds_for(self, exc: ProviderError) -> int:
        if exc.cooldown_seconds is not None:
            return max(1, int(exc.cooldown_seconds))
        return max(1, int(self._cooldown_seconds * getattr(exc, "cooldown_multiplier", 1.0)))

    def _mark_failure(self, name: str, exc: ProviderError) -> int:
        rec = self._health[name]
        rec.consecutive_failures += 1
        rec.last_failure_at = time.time()
        rec.last_failure_reason = str(exc)
        rec.last_failure_type = getattr(exc, "failure_type", "provider_error")
        rec.last_cooldown_seconds = 0

        should_cooldown = getattr(exc, "immediate_cooldown", False) or rec.consecutive_failures >= self._failure_threshold
        if should_cooldown:
            cooldown_seconds = self._cooldown_seconds_for(exc)
            rec.cooldown_until = time.time() + cooldown_seconds
            rec.last_cooldown_seconds = cooldown_seconds
            return cooldown_seconds
        return 0

    def _mark_empty_result(self, name: str) -> None:
        # Empty responses do not trigger cooldown on their own,
        # but they are tracked so operators can observe provider behavior
        # and distinguish content gaps from hard failures.
        rec = self._health[name]
        rec.consecutive_empty_results += 1
        rec.last_failure_at = time.time()
        rec.last_failure_reason = "empty_results"

    async def routed_search(
        self,
        query: str,
        options: Dict[str, Any],
        max_attempts: int,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        attempts = 0
        provider_trace: List[Dict[str, Any]] = []
        request_id = options.get("request_id", "-")

        logger.info(
            "event=provider_router_start request_id=%s query=%r max_attempts=%s providers=%s",
            request_id,
            query,
            max_attempts,
            len(self._slots),
        )

        mode = options.get("mode")
        for slot in self._pick_order(mode=mode):
            if attempts >= max_attempts:
                break

            rec = self._health[slot.provider.name]
            if not self._is_ready(rec):
                logger.info(
                    "event=provider_skip request_id=%s provider=%s status=cooldown cooldown_until=%s",
                    request_id,
                    slot.provider.name,
                    rec.cooldown_until,
                )
                provider_trace.append(
                    {
                        "provider": slot.provider.name,
                        "status": "skipped_cooldown",
                        "cooldown_until": rec.cooldown_until,
                    }
                )
                continue

            attempts += 1
            started = time.perf_counter()
            try:
                logger.info(
                    "event=provider_attempt request_id=%s provider=%s attempt=%s query=%r",
                    request_id,
                    slot.provider.name,
                    attempts,
                    query,
                )
                rows = await slot.provider.search(query, options)
                logger.info(
                    "event=provider_success request_id=%s provider=%s attempt=%s result_count=%s latency_ms=%s",
                    request_id,
                    slot.provider.name,
                    attempts,
                    len(rows),
                    int((time.perf_counter() - started) * 1000),
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                provider_trace.append(
                    {
                        "provider": slot.provider.name,
                        "status": "success" if rows else "empty",
                        "result_count": len(rows),
                        "attempt": attempts,
                        "latency_ms": latency_ms,
                    }
                )
                if rows:
                    self._mark_success(slot.provider.name)
                    return rows, provider_trace

                self._mark_empty_result(slot.provider.name)
                logger.warning(
                    "event=provider_empty request_id=%s provider=%s attempt=%s latency_ms=%s",
                    request_id,
                    slot.provider.name,
                    attempts,
                    latency_ms,
                )
            except ProviderError as exc:
                cooldown_seconds = self._mark_failure(slot.provider.name, exc)
                status = getattr(exc, "failure_type", "provider_error")
                logger.warning(
                    "event=provider_failed request_id=%s provider=%s attempt=%s status=%s error=%s cooldown_seconds=%s",
                    request_id,
                    slot.provider.name,
                    attempts,
                    status,
                    exc,
                    cooldown_seconds,
                )
                provider_trace.append(
                    {
                        "provider": slot.provider.name,
                        "status": status,
                        "attempt": attempts,
                        "error": str(exc),
                        "cooldown_seconds": cooldown_seconds,
                    }
                )

        logger.info("event=provider_router_end request_id=%s query=%r attempts=%s", request_id, query, attempts)
        return [], provider_trace
