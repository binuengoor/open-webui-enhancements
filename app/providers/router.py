from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Tuple

from app.models.contracts import ProviderHealthRecord
from app.providers.base import ProviderError, RateLimitError, SearchProvider


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

    def _is_ready(self, rec: ProviderHealthRecord) -> bool:
        return rec.enabled and rec.cooldown_until <= time.time()

    def _pick_order(self) -> List[ProviderSlot]:
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

            # Keep weighted preference but avoid trying the same provider
            # multiple times in a single routed_search call.
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
        rec.last_success_at = time.time()
        rec.last_failure_reason = ""

    def _mark_failure(self, name: str, reason: str, rate_limited: bool = False) -> None:
        rec = self._health[name]
        rec.consecutive_failures += 1
        rec.last_failure_at = time.time()
        rec.last_failure_reason = reason
        if rate_limited or rec.consecutive_failures >= self._failure_threshold:
            rec.cooldown_until = time.time() + self._cooldown_seconds

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

        for slot in self._pick_order():
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
            except RateLimitError as exc:
                self._mark_failure(slot.provider.name, str(exc), rate_limited=True)
                logger.warning(
                    "event=provider_rate_limited request_id=%s provider=%s attempt=%s error=%s",
                    request_id,
                    slot.provider.name,
                    attempts,
                    exc,
                )
                provider_trace.append(
                    {
                        "provider": slot.provider.name,
                        "status": "rate_limited",
                        "attempt": attempts,
                        "error": str(exc),
                    }
                )
            except ProviderError as exc:
                self._mark_failure(slot.provider.name, str(exc), rate_limited=False)
                logger.warning(
                    "event=provider_failed request_id=%s provider=%s attempt=%s error=%s",
                    request_id,
                    slot.provider.name,
                    attempts,
                    exc,
                )
                provider_trace.append(
                    {
                        "provider": slot.provider.name,
                        "status": "failed",
                        "attempt": attempts,
                        "error": str(exc),
                    }
                )

            logger.info("event=provider_router_end request_id=%s query=%r attempts=%s", request_id, query, attempts)
        return [], provider_trace
