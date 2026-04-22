from __future__ import annotations

import unittest

from app.providers.base import AuthProviderError, RateLimitError, SearchProvider, TransientProviderError
from app.providers.router import ProviderRouter, ProviderSlot


class DummyProvider(SearchProvider):
    def __init__(self, name: str, responses=None):
        self.name = name
        self._responses = list(responses or [])
        self.calls = []

    async def search(self, query, options):
        self.calls.append({"query": query, "options": dict(options)})
        if not self._responses:
            return []

        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ProviderRouterFailurePolicyTests(unittest.TestCase):
    def setUp(self):
        self.router = ProviderRouter(
            slots=[ProviderSlot(provider=DummyProvider("dummy"), weight=1, enabled=True)],
            cooldown_seconds=10,
            failure_threshold=2,
        )

    def test_rate_limit_uses_retry_after_override(self):
        cooldown = self.router._mark_failure("dummy", RateLimitError("limited", cooldown_seconds=17))
        rec = self.router.health_snapshot()[0]

        self.assertEqual(cooldown, 17)
        self.assertEqual(rec.last_failure_type, "rate_limit")
        self.assertEqual(rec.last_cooldown_seconds, 17)
        self.assertGreater(rec.cooldown_until, 0)

    def test_auth_failure_immediately_cools_down_longer(self):
        cooldown = self.router._mark_failure("dummy", AuthProviderError("bad auth"))
        rec = self.router.health_snapshot()[0]

        self.assertEqual(cooldown, 30)
        self.assertEqual(rec.last_failure_type, "auth")
        self.assertEqual(rec.last_cooldown_seconds, 30)

    def test_transient_failure_waits_for_threshold(self):
        first = self.router._mark_failure("dummy", TransientProviderError("timeout"))
        rec = self.router.health_snapshot()[0]
        self.assertEqual(first, 0)
        self.assertEqual(rec.last_failure_type, "transient")
        self.assertEqual(rec.last_cooldown_seconds, 0)

        second = self.router._mark_failure("dummy", TransientProviderError("timeout again"))
        rec = self.router.health_snapshot()[0]
        self.assertEqual(second, 10)
        self.assertEqual(rec.last_cooldown_seconds, 10)

    def test_success_resets_failure_and_cooldown_state(self):
        self.router._mark_failure("dummy", RateLimitError("limited", cooldown_seconds=17))
        self.router._mark_success("dummy")
        rec = self.router.health_snapshot()[0]

        self.assertEqual(rec.consecutive_failures, 0)
        self.assertEqual(rec.last_failure_reason, "")
        self.assertEqual(rec.last_failure_type, "")
        self.assertEqual(rec.last_cooldown_seconds, 0)
        self.assertEqual(rec.cooldown_until, 0.0)


class ProviderRouterRoutedSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_routed_search_falls_through_empty_then_returns_next_provider_results(self):
        primary = DummyProvider("primary", responses=[[]])
        backup = DummyProvider("backup", responses=[[{"url": "https://example.com", "title": "hit", "snippet": "ok"}]])
        router = ProviderRouter(
            slots=[
                ProviderSlot(provider=primary, weight=1, enabled=True),
                ProviderSlot(provider=backup, weight=1, enabled=True),
            ],
            cooldown_seconds=10,
            failure_threshold=2,
        )

        rows, trace = await router.routed_search("test query", {"request_id": "r1"}, max_attempts=2)
        health = {rec.name: rec for rec in router.health_snapshot()}

        self.assertEqual(rows[0]["title"], "hit")
        self.assertEqual([entry["provider"] for entry in trace], ["primary", "backup"])
        self.assertEqual([entry["status"] for entry in trace], ["empty", "success"])
        self.assertEqual(health["primary"].consecutive_empty_results, 1)
        self.assertEqual(health["primary"].consecutive_failures, 0)
        self.assertEqual(health["backup"].consecutive_failures, 0)

    async def test_routed_search_records_mixed_failure_and_success_trace(self):
        primary = DummyProvider("primary", responses=[TransientProviderError("timeout")])
        backup = DummyProvider("backup", responses=[[{"url": "https://example.com", "title": "fallback", "snippet": "ok", "provider": "backup"}]])
        router = ProviderRouter(
            slots=[
                ProviderSlot(provider=primary, weight=1, enabled=True),
                ProviderSlot(provider=backup, weight=1, enabled=True),
            ],
            cooldown_seconds=10,
            failure_threshold=2,
        )

        rows, trace = await router.routed_search("test query", {"request_id": "r2"}, max_attempts=2)
        health = {rec.name: rec for rec in router.health_snapshot()}

        self.assertEqual(rows[0]["provider"], "backup")
        self.assertEqual(trace[0]["provider"], "primary")
        self.assertEqual(trace[0]["status"], "transient")
        self.assertEqual(trace[0]["cooldown_seconds"], 0)
        self.assertEqual(trace[1]["provider"], "backup")
        self.assertEqual(trace[1]["status"], "success")
        self.assertEqual(health["primary"].consecutive_failures, 1)
        self.assertEqual(health["primary"].cooldown_until, 0.0)
        self.assertEqual(health["backup"].last_failure_reason, "")

    async def test_routed_search_skips_provider_in_cooldown_without_spending_attempt(self):
        limited = DummyProvider("limited")
        healthy = DummyProvider("healthy", responses=[[{"url": "https://example.com", "title": "healthy", "snippet": "ok", "provider": "healthy"}]])
        router = ProviderRouter(
            slots=[
                ProviderSlot(provider=limited, weight=1, enabled=True),
                ProviderSlot(provider=healthy, weight=1, enabled=True),
            ],
            cooldown_seconds=10,
            failure_threshold=1,
        )
        router._mark_failure("limited", RateLimitError("limited", cooldown_seconds=17))

        rows, trace = await router.routed_search("test query", {"request_id": "r3"}, max_attempts=1)

        self.assertEqual(rows[0]["provider"], "healthy")
        self.assertEqual(trace[0]["provider"], "limited")
        self.assertEqual(trace[0]["status"], "skipped_cooldown")
        self.assertEqual(trace[1]["provider"], "healthy")
        self.assertEqual(trace[1]["attempt"], 1)
        self.assertEqual(len(limited.calls), 0)
        self.assertEqual(len(healthy.calls), 1)

    def test_pick_order_prioritizes_preferred_and_deprioritizes_avoided_for_mode(self):
        alpha = DummyProvider("alpha")
        beta = DummyProvider("beta")
        gamma = DummyProvider("gamma")
        router = ProviderRouter(
            slots=[
                ProviderSlot(provider=alpha, weight=1, enabled=True),
                ProviderSlot(provider=beta, weight=1, enabled=True),
                ProviderSlot(provider=gamma, weight=1, enabled=True),
            ],
            cooldown_seconds=10,
            failure_threshold=2,
            provider_preferences={"research": {"prefer": ["gamma"], "avoid": ["alpha"]}},
        )

        ordered = router._pick_order(mode="research")

        self.assertEqual([slot.provider.name for slot in ordered], ["gamma", "beta", "alpha"])

    def test_pick_order_preserves_rotation_within_preferred_group(self):
        alpha = DummyProvider("alpha")
        beta = DummyProvider("beta")
        gamma = DummyProvider("gamma")
        router = ProviderRouter(
            slots=[
                ProviderSlot(provider=alpha, weight=1, enabled=True),
                ProviderSlot(provider=beta, weight=1, enabled=True),
                ProviderSlot(provider=gamma, weight=1, enabled=True),
            ],
            cooldown_seconds=10,
            failure_threshold=2,
            provider_preferences={"research": {"prefer": ["alpha", "beta"]}},
        )
        router._cursor = 1

        ordered = router._pick_order(mode="research")

        self.assertEqual([slot.provider.name for slot in ordered], ["beta", "alpha", "gamma"])


if __name__ == "__main__":
    unittest.main()
