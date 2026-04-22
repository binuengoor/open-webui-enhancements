from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.models.contracts import PerplexitySearchRequest


class SearchModeContractTests(unittest.TestCase):
    def test_perplexity_request_normalizes_auto_to_default_behavior(self):
        payload = PerplexitySearchRequest(query="latest postgres release", search_mode="auto")

        self.assertIsNone(payload.search_mode)

    def test_search_route_accepts_auto_search_mode(self):
        captured = {}

        async def execute_perplexity_search(payload):
            captured["search_mode"] = payload.search_mode
            return SimpleNamespace(
                model_dump=lambda exclude_none=True: {
                    "id": "search-1",
                    "results": [
                        {
                            "title": "Example",
                            "url": "https://example.com",
                            "snippet": "Example snippet",
                        }
                    ],
                }
            )

        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = SimpleNamespace(
            execute_perplexity_search=execute_perplexity_search,
            record_failed_run=lambda **kwargs: None,
        )

        client = TestClient(app)
        response = client.post(
            "/search",
            json={"query": "latest postgres release", "search_mode": "auto"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(captured["search_mode"])
        self.assertEqual(response.json()["results"][0]["url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
