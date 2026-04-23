from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.cache.memory_cache import InMemoryCache
from app.models.contracts import SearchRequest
from app.services.orchestrator import ResearchOrchestrator
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker
from app.services.run_history import RecentRunHistory


class StubRouter:
    def health_snapshot(self):
        return []

    async def routed_search(self, query, options, max_attempts=1):
        return (
            [
                {
                    "url": "https://example.com/a",
                    "title": f"Result for {query}",
                    "snippet": "Grounded snippet",
                    "provider": "stub",
                }
            ],
            [{"provider": "stub", "status": "success"}],
        )


class StubFetcher:
    async def fetch(self, url):
        return {
            "url": url,
            "title": "Example result",
            "content": "Example result content with enough detail to synthesize a grounded answer.",
            "published_at": "",
            "last_updated": None,
            "language": "en",
            "source": "example.com",
        }

    async def extract(self, url):
        return {"url": url}


class RunHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = SimpleNamespace(
            search_limits=SimpleNamespace(max_provider_attempts=1, max_pages_to_fetch=4),
            vane=SimpleNamespace(enabled=False),
            cache=SimpleNamespace(ttl_general_s=300, ttl_recency_s=45, page_cache_ttl_s=120),
            research_llm_ready=True,
            research_llm_requirement_error="",
        )
        self.history = RecentRunHistory(max_entries=2)
        self.orchestrator = ResearchOrchestrator(
            config=config,
            router=StubRouter(),
            search_cache=InMemoryCache(max_entries=8),
            page_cache=InMemoryCache(max_entries=8),
            fetcher=StubFetcher(),
            planner=QueryPlanner(),
            ranker=Ranker(),
            run_history=self.history,
        )

    async def test_records_recent_search_runs_with_debug_fields(self):
        response = await self.orchestrator.execute_search(
            SearchRequest(query="python release date", mode="fast", include_citations=True),
            endpoint="/search",
        )

        runs = self.orchestrator.recent_runs(limit=10)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["endpoint"], "/search")
        self.assertEqual(runs[0]["query"], "python release date")
        self.assertEqual(runs[0]["mode"], response.mode)
        self.assertEqual(runs[0]["citations_count"], len(response.citations))
        self.assertEqual(runs[0]["sources_count"], len(response.sources))
        self.assertIn(runs[0]["confidence"], {"low", "medium", "high"})
        self.assertIsInstance(runs[0]["timestamp"], str)

    async def test_ring_buffer_keeps_only_most_recent_entries(self):
        await self.orchestrator.execute_search(SearchRequest(query="first", mode="fast"), endpoint="/search")
        await self.orchestrator.execute_search(SearchRequest(query="second", mode="fast"), endpoint="/search")
        await self.orchestrator.execute_search(SearchRequest(query="third", mode="research"), endpoint="/research")

        runs = self.orchestrator.recent_runs(limit=10)

        self.assertEqual([item["query"] for item in runs], ["third", "second"])
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()
