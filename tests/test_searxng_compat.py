from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.models.contracts import PerplexitySearchResponse, PerplexitySearchResult, SearxngCompatRequest
from app.providers.router import ProviderRouter, ProviderSlot
from app.services.searxng_compat import SearxngCompatService


class DummyProvider:
    def __init__(self, name: str, base_url: str = "https://searx.example", timeout_s: int = 12):
        self.name = name
        self.base_url = base_url
        self.timeout_s = timeout_s


class SearxngCompatRequestTests(unittest.TestCase):
    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            SearxngCompatRequest(q="")

    def test_non_json_format_rejected(self):
        with self.assertRaises(ValueError):
            SearxngCompatRequest(q="test", format="html")

    def test_engine_and_category_lists_are_normalized(self):
        payload = SearxngCompatRequest(q="cats", categories=" images, general ", engines=" youtube, google images ")

        self.assertEqual(payload.categories_list, ["images", "general"])
        self.assertEqual(payload.engines_list, ["youtube", "google images"])


class SearxngCompatServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_requests_map_provider_results_to_searxng_shape(self):
        router = ProviderRouter([ProviderSlot(provider=DummyProvider("searxng"), weight=1, enabled=True)], 10, 1)

        async def fake_search_once(**kwargs):
            return (
                [{"title": "Example", "url": "https://example.com", "snippet": "Snippet", "provider": "brave-search"}],
                [{"provider": "brave-search", "status": "success"}],
                {"hits": 0, "misses": 1},
            )

        orch = SimpleNamespace(_search_once=fake_search_once)
        config = SimpleNamespace(modes={"fast": SimpleNamespace(max_provider_attempts=2)})
        service = SearxngCompatService(config=config, orchestrator=orch, router=router)

        response = await service.execute(SearxngCompatRequest(q="example", format="json"))

        self.assertEqual(response.query, "example")
        self.assertEqual(response.number_of_results, 1)
        self.assertEqual(response.results[0].title, "Example")
        self.assertEqual(response.results[0].content, "Snippet")
        self.assertEqual(response.results[0].engine, "brave-search")
        self.assertEqual(response.suggestions, [])

    async def test_provider_failure_returns_http_compatible_empty_payload(self):
        router = ProviderRouter([ProviderSlot(provider=DummyProvider("searxng"), weight=1, enabled=True)], 10, 1)

        async def fake_search_once(**kwargs):
            return (
                [],
                [{"provider": "brave-search", "status": "transient"}],
                {"hits": 0, "misses": 1},
            )

        orch = SimpleNamespace(_search_once=fake_search_once)
        config = SimpleNamespace(modes={"fast": SimpleNamespace(max_provider_attempts=2)})
        service = SearxngCompatService(config=config, orchestrator=orch, router=router)

        response = await service.execute(SearxngCompatRequest(q="example"))

        self.assertEqual(response.number_of_results, 0)
        self.assertEqual(response.results, [])
        self.assertEqual(response.unresponsive_engines, ["brave-search"])

    async def test_media_requests_passthrough_for_images_and_videos(self):
        router = ProviderRouter([ProviderSlot(provider=DummyProvider("searxng", base_url="https://searx.example"), weight=1, enabled=True)], 10, 1)
        orch = SimpleNamespace(_search_once=None)
        config = SimpleNamespace(modes={"fast": SimpleNamespace(max_provider_attempts=2)})
        service = SearxngCompatService(config=config, orchestrator=orch, router=router)

        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "query": "cats",
                    "number_of_results": 1,
                    "results": [{"title": "Cat video", "url": "https://youtube.com/watch?v=1", "thumbnail": "https://img/1.jpg", "iframe_src": "https://youtube.com/embed/1"}],
                    "suggestions": [],
                    "answers": [],
                    "corrections": [],
                    "infoboxes": [],
                    "unresponsive_engines": [],
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.calls = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False
            async def get(self, url, params=None):
                self.calls.append((url, params))
                return FakeResponse()

        with patch("app.services.searxng_compat.httpx.AsyncClient", FakeClient):
            images = await service.execute(SearxngCompatRequest(q="cats", engines="google images"))
            videos = await service.execute(SearxngCompatRequest(q="cats", engines="youtube"))

        self.assertEqual(images.results[0].title, "Cat video")
        self.assertEqual(videos.results[0].iframe_src, "https://youtube.com/embed/1")

    async def test_media_detection_uses_category_or_engine_tokens(self):
        router = ProviderRouter([ProviderSlot(provider=DummyProvider("searxng"), weight=1, enabled=True)], 10, 1)
        orch = SimpleNamespace(_search_once=None)
        config = SimpleNamespace(modes={"fast": SimpleNamespace(max_provider_attempts=2)})
        service = SearxngCompatService(config=config, orchestrator=orch, router=router)

        self.assertEqual(service._detect_vertical(SearxngCompatRequest(q="q", categories="images")).vertical, "images")
        self.assertEqual(service._detect_vertical(SearxngCompatRequest(q="q", categories="videos")).vertical, "videos")
        self.assertEqual(service._detect_vertical(SearxngCompatRequest(q="q", engines="youtube")).vertical, "videos")
        self.assertEqual(service._detect_vertical(SearxngCompatRequest(q="q", engines="google images")).vertical, "images")


class SearxngCompatRouteTests(unittest.TestCase):
    def _client(self):
        app = FastAPI()
        app.include_router(router)

        async def execute_perplexity_search(payload):
            return PerplexitySearchResponse(
                id="search-1",
                results=[PerplexitySearchResult(title="Perplexity", url="https://example.com/p", snippet="Snippet")],
            )

        async def execute_search(payload, endpoint="/research"):
            return SimpleNamespace(model_dump=lambda: {"query": payload.query, "mode": "research"})

        async def search_once(**kwargs):
            return (
                [{"title": "Compat", "url": "https://example.com/c", "snippet": "Compat snippet", "provider": "searxng"}],
                [{"provider": "searxng", "status": "success"}],
                {"hits": 0, "misses": 1},
            )

        app.state.orchestrator = SimpleNamespace(
            execute_perplexity_search=execute_perplexity_search,
            execute_search=execute_search,
            _search_once=search_once,
            record_failed_run=lambda **kwargs: None,
        )
        app.state.config = SimpleNamespace(modes={"fast": SimpleNamespace(max_provider_attempts=2)})
        app.state.provider_router = ProviderRouter([ProviderSlot(provider=DummyProvider("searxng"), weight=1, enabled=True)], 10, 1)
        return TestClient(app)

    def test_route_returns_json_envelope(self):
        client = self._client()

        response = client.get("/compat/searxng", params={"q": "example", "format": "json"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["query"], "example")
        self.assertEqual(body["results"][0]["content"], "Compat snippet")
        self.assertEqual(body["suggestions"], [])

    def test_route_independent_from_search_contract(self):
        client = self._client()

        compat = client.get("/compat/searxng", params={"q": "example"}).json()
        search = client.post("/search", json={"query": "example"}).json()

        self.assertIn("query", compat)
        self.assertIn("number_of_results", compat)
        self.assertNotIn("id", compat)
        self.assertIn("id", search)
        self.assertNotIn("number_of_results", search)


if __name__ == "__main__":
    unittest.main()
