from __future__ import annotations

import unittest

from app.providers.base import AuthProviderError, RateLimitError, SearchProvider, TransientProviderError
from app.providers.router import ProviderRouter, ProviderSlot


class DummyProvider(SearchProvider):
    def __init__(self, name: str):
        self.name = name

    async def search(self, query, options):
        return []


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


if __name__ == "__main__":
    unittest.main()
