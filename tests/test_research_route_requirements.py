from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.api.routes import router


class ResearchRouteRequirementTests(unittest.TestCase):
    def test_research_route_rejects_when_vane_proxy_is_unavailable(self):
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = SimpleNamespace()
        app.state.config = SimpleNamespace()
        app.state.provider_router = SimpleNamespace()

        class _Proxy:
            async def streaming_response(self, payload):
                raise Exception("should be handled by service test, not route")

            def ensure_ready(self):
                raise ValueError("unused")

        class _RejectingProxy:
            async def streaming_response(self, payload):
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail="research mode requires Vane proxy configuration")

        app.state.research_proxy = _RejectingProxy()

        client = TestClient(app)
        response = client.post("/research", json={"query": "compare postgres and mysql"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Vane proxy configuration", response.json()["detail"])

    def test_research_route_returns_streaming_response_from_proxy(self):
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = SimpleNamespace()
        app.state.config = SimpleNamespace()
        app.state.provider_router = SimpleNamespace()

        class _Proxy:
            async def streaming_response(self, payload):
                async def body():
                    yield b"event: message\n"
                    yield b"data: hello\n\n"

                return StreamingResponse(body(), media_type="text/event-stream")

        app.state.research_proxy = _Proxy()

        client = TestClient(app)
        response = client.post("/research", json={"query": "compare postgres and mysql"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        self.assertIn("event: message", response.text)


if __name__ == "__main__":
    unittest.main()
