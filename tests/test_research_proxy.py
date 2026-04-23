from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.models.contracts import ResearchRequest
from app.services.research_proxy import ResearchProxyService


class _StubAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        return None


class ResearchProxyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_upstream_payload_maps_request_fields(self):
        service = ResearchProxyService(
            config=SimpleNamespace(
                research_llm_ready=True,
                research_llm_requirement_error="not ready",
                vane=SimpleNamespace(
                    url="http://vane.local",
                    timeout_s=10,
                    chat_provider_id="openai",
                    chat_model_key="auto-main",
                    embedding_provider_id="ollama",
                    embedding_model_key="nomic",
                ),
            )
        )

        payload = service.build_upstream_payload(
            ResearchRequest(query="test query", source_mode="all", depth="quick")
        )

        self.assertEqual(payload["query"], "test query")
        self.assertEqual(payload["sources"], ["web", "academic", "discussions"])
        self.assertEqual(payload["optimizationMode"], "speed")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["chatModel"]["providerId"], "openai")
        self.assertEqual(payload["embeddingModel"]["providerId"], "ollama")

    async def test_stream_research_maps_timeout_to_504(self):
        service = ResearchProxyService(
            config=SimpleNamespace(
                research_llm_ready=True,
                research_llm_requirement_error="not ready",
                vane=SimpleNamespace(
                    url="http://vane.local",
                    timeout_s=10,
                    chat_provider_id="openai",
                    chat_model_key="auto-main",
                    embedding_provider_id="ollama",
                    embedding_model_key="nomic",
                ),
            )
        )

        original_client = httpx.AsyncClient

        class _TimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, json):
                return httpx.Request(method, url, json=json)

            async def send(self, request, stream=False):
                raise httpx.TimeoutException("boom")

            async def aclose(self):
                return None

        httpx.AsyncClient = _TimeoutClient
        try:
            with self.assertRaises(Exception) as ctx:
                await service.stream_research(ResearchRequest(query="timeout test"))
        finally:
            httpx.AsyncClient = original_client

        self.assertEqual(ctx.exception.status_code, 504)

    async def test_stream_research_returns_passthrough_stream(self):
        service = ResearchProxyService(
            config=SimpleNamespace(
                research_llm_ready=True,
                research_llm_requirement_error="not ready",
                vane=SimpleNamespace(
                    url="http://vane.local",
                    timeout_s=10,
                    chat_provider_id="openai",
                    chat_model_key="auto-main",
                    embedding_provider_id="ollama",
                    embedding_model_key="nomic",
                ),
            )
        )

        original_client = httpx.AsyncClient

        class _SuccessClient:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, json):
                return httpx.Request(method, url, json=json)

            async def send(self, request, stream=False):
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream", "x-request-id": "upstream-1"},
                    stream=_StubAsyncByteStream([b"event: message\n", b"data: hello\n\n"]),
                    request=request,
                )

            async def aclose(self):
                return None

        httpx.AsyncClient = _SuccessClient
        try:
            upstream = await service.stream_research(ResearchRequest(query="stream test"))
            chunks = []
            async for chunk in upstream.body:
                chunks.append(chunk)
            await upstream.background()
        finally:
            httpx.AsyncClient = original_client

        self.assertEqual(upstream.status_code, 200)
        self.assertEqual(upstream.headers["content-type"], "text/event-stream")
        self.assertEqual(b"".join(chunks), b"event: message\ndata: hello\n\n")


class ResearchRouteProxyTests(unittest.TestCase):
    def test_research_route_uses_proxy_service(self):
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = SimpleNamespace()

        class _Proxy:
            def __init__(self):
                self.calls = []

            async def streaming_response(self, payload):
                self.calls.append(payload)
                return SimpleNamespace(status_code=200)

        proxy = _Proxy()
        app.state.research_proxy = proxy
        app.state.config = SimpleNamespace()
        app.state.provider_router = SimpleNamespace()

        client = TestClient(app)
        response = client.post("/research", json={"query": "proxy me"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(proxy.calls), 1)
        self.assertEqual(proxy.calls[0].query, "proxy me")

    def test_internal_search_is_deprecated(self):
        app = FastAPI()
        app.include_router(router)

        class _Orch:
            async def execute_search(self, payload, endpoint="/search"):
                return SimpleNamespace(model_dump=lambda: {"query": payload.query, "endpoint": endpoint})

        app.state.orchestrator = _Orch()
        app.state.config = SimpleNamespace()
        app.state.provider_router = SimpleNamespace()
        app.state.research_proxy = SimpleNamespace()

        client = TestClient(app)
        response = client.post("/internal/search", json={"query": "deprecated", "mode": "fast"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["endpoint"], "/search")
        self.assertIn("deprecated", response.json()["warnings"][0])


if __name__ == "__main__":
    unittest.main()
