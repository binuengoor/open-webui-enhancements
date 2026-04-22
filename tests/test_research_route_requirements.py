from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router


class ResearchRouteRequirementTests(unittest.TestCase):
    def test_research_route_rejects_when_llm_support_is_unavailable(self):
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = SimpleNamespace(
            ensure_research_llm_available=lambda: (_ for _ in ()).throw(
                ValueError("research mode requires LLM support")
            )
        )

        client = TestClient(app)
        response = client.post("/research", json={"query": "compare postgres and mysql"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("research mode requires LLM support", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
