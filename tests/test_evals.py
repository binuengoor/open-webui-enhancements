from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from app.cache.memory_cache import InMemoryCache
from app.models.contracts import SearchRequest
from app.providers.base import SearchProvider
from app.providers.router import ProviderRouter, ProviderSlot
from app.services.fetcher import PageFetcher
from app.services.orchestrator import ResearchOrchestrator
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker
from tests.eval_gates import score_response


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "evals"


class FixtureProvider(SearchProvider):
    def __init__(self, dataset: Dict[str, Dict[str, Any]]):
        self.name = "fixture-provider"
        self._dataset = dataset

    async def search(self, query: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        query_payload = self._dataset[query]
        rows = []
        for rank, page in enumerate(query_payload["search_rows"], start=1):
            row = dict(page)
            row.setdefault("provider", self.name)
            row.setdefault("rank", rank)
            rows.append(row)
        return rows


class FixtureFetcher(PageFetcher):
    def __init__(self, dataset: Dict[str, Dict[str, Any]]):
        super().__init__(timeout_s=1, max_chars=4000, flaresolverr_url="", user_agent="fixture-fetcher")
        self._pages = {}
        for query_payload in dataset.values():
            for page in query_payload["pages"]:
                self._pages[page["url"]] = dict(page)

    async def fetch(self, url: str) -> Dict[str, Any]:
        page = dict(self._pages[url])
        page.setdefault("error", None)
        page.setdefault("source", "html")
        page.setdefault("language", "en")
        page.setdefault("published_at", "")
        page.setdefault("last_updated", "")
        return page

    async def extract(self, url: str) -> Dict[str, Any]:
        return {"url": url, "title": self._pages[url].get("title", ""), "headings": [], "links": [], "error": None}


def load_eval_fixtures() -> List[Dict[str, Any]]:
    fixtures = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        # Skip baseline/comparison artifacts that are not scenario fixtures.
        if "baseline" in path.stem:
            continue
        fixture = json.loads(path.read_text(encoding="utf-8"))
        # Normalise two known schemas into a canonical shape:
        # - fixture-generator schema: {scenario, query, category, ...}
        # - rich standalone schema: {name, request: {query, depth, ...}, ...}
        if "scenario" in fixture:
            # Fixture-generator schema — already compatible.
            canonical = fixture
        elif "request" in fixture:
            # Rich standalone schema — fold into fixture-generator shape.
            req = fixture.get("request", {})
            canonical = {
                "id": fixture.get("name", fixture.get("name", path.stem)),
                "scenario": "standalone",
                "query": req.get("query", ""),
                "category": "standalone",
                "endpoint": "/research",
                "request": req,
                "pages": fixture.get("pages", []),
                "provider_rows": fixture.get("provider_rows", []),
                "gates": fixture.get("gates", {}),
            }
        else:
            # Unknown schema — skip rather than crash.
            continue
        canonical["fixture_path"] = path
        fixtures.append(canonical)
    return fixtures


def build_dataset(fixtures: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    dataset: Dict[str, Dict[str, Any]] = {}
    for fixture in fixtures:
        scenario = fixture.get("scenario", "standalone")
        query = fixture["query"]
        if scenario == "negative_generic":
            dataset[query] = _negative_payload(query)
        elif scenario == "standalone":
            # Rich standalone fixture brings its own provider_rows and pages.
            dataset[query] = {
                "search_rows": fixture.get("provider_rows", []),
                "pages": fixture.get("pages", []),
                "forced_confidence": 0.85,  # default to strong confidence.
            }
        else:
            dataset[query] = _strong_payload(query, scenario)
    return dataset


def _strong_payload(query: str, scenario: str) -> Dict[str, Any]:
    slug = _slug(query)
    if scenario == "strong_search":
        count = 2
        confidence = 0.72
    elif scenario in {"recency_search", "sparse_research"}:
        count = 3 if scenario == "recency_search" else 2
        confidence = 0.61
    else:
        count = 4
        confidence = 0.84

    pages = []
    for idx in range(1, count + 1):
        url = f"https://evidence{idx}.example.com/{slug}"
        title = f"{query} source {idx}"
        content = (
            f"{query} source {idx} provides grounded evidence with concrete details, dates, tradeoffs, and implementation notes. "
            f"This passage is specific to {query} and includes enough context for synthesis. "
            f"Additional source {idx} notes keep the evidence distinct and reviewable."
        )
        if scenario.startswith("contradiction") and idx % 2 == 0:
            content += " Some sources disagree on the net outcome, so the answer should acknowledge disagreement and scope limits."
        if scenario.startswith("sparse"):
            content += " Public evidence is limited, so conclusions should remain cautious and provisional."
        if scenario.startswith("recency"):
            content += " Recency matters here because older summaries can go stale quickly."
        pages.append(
            {
                "url": url,
                "title": title,
                "content": content,
                "snippet": content[:180],
                "source": "html",
                "language": "en",
                "published_at": "2026-04-01" if scenario.startswith("recency") else "2025-01-01",
                "last_updated": "2026-04-15" if scenario.startswith("recency") else "2025-02-01",
            }
        )

    return {"search_rows": pages, "pages": pages, "forced_confidence": confidence}


def _negative_payload(query: str) -> Dict[str, Any]:
    page = {
        "url": f"https://weak.example.com/{_slug(query)}",
        "title": f"{query} generic advice",
        "content": "It depends on your needs and budget. Consider tradeoffs and choose what fits best.",
        "snippet": "It depends on your needs and budget.",
        "source": "html",
        "language": "en",
        "published_at": "2024-01-01",
        "last_updated": "2024-01-02",
    }
    return {"search_rows": [page], "pages": [page], "forced_confidence": 0.2}


def _slug(text: str) -> str:
    return "-".join(part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()[:8])


def evaluate_response(fixture: Dict[str, Any], response) -> Dict[str, Any]:
    # Minimum citation threshold depends on endpoint and scenario.
    scenario = fixture.get("scenario", "standalone")
    endpoint = fixture.get("endpoint", "/research")
    if scenario == "sparse_research":
        min_citations = 2
    elif endpoint == "/search":
        min_citations = 2
    else:
        min_citations = 3

    failures = []
    if len(response.citations) < min_citations:
        failures.append(f"citation_count<{min_citations}")
    if response.diagnostics.errors:
        failures.append("has_errors")
    if response.confidence == "low":
        failures.append("confidence_low")

    # Run the full gate suite so we prove the MP-08 acceptance surface.
    gate_results = score_response(response, fixture.get("gates", {}))
    for gate_result in gate_results.gates:
        if not gate_result.passed:
            failures.append(f"gate:{gate_result.name}:{gate_result.reason}")

    return {
        "fixture_id": fixture.get("id", fixture.get("query", "unknown")[:50]),
        "category": fixture.get("category", "unknown"),
        "endpoint": endpoint,
        "min_citations": min_citations,
        "citation_count": len(response.citations),
        "source_count": len(response.sources),
        "finding_count": len(response.findings),
        "errors": list(response.diagnostics.errors),
        "warnings": list(response.diagnostics.warnings or []),
        "confidence": response.confidence,
        "gate_results": {r.name: {"pass": r.passed, "reason": r.reason} for r in gate_results.gates},
        "passed": not failures,
        "failures": failures,
    }


class EvalRunnerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = load_eval_fixtures()
        cls.dataset = build_dataset(cls.fixtures)

    def setUp(self) -> None:
        provider = FixtureProvider(self.dataset)
        router = ProviderRouter(
            slots=[ProviderSlot(provider=provider, weight=1, enabled=True)],
            cooldown_seconds=5,
            failure_threshold=2,
        )
        config = SimpleNamespace(
            search_limits=SimpleNamespace(max_provider_attempts=1, max_pages_to_fetch=4),
            vane=SimpleNamespace(enabled=False),
            cache=SimpleNamespace(ttl_general_s=300, ttl_recency_s=45, page_cache_ttl_s=120),
        )
        self.fetcher = FixtureFetcher(self.dataset)
        self.orchestrator = ResearchOrchestrator(
            config=config,
            router=router,
            search_cache=InMemoryCache(max_entries=32),
            page_cache=InMemoryCache(max_entries=32),
            fetcher=self.fetcher,
            planner=QueryPlanner(),
            ranker=Ranker(),
        )

    async def _run_fixture(self, fixture: Dict[str, Any]):
        mode = "fast" if fixture["endpoint"] == "/search" else "research"
        # source_mode lives at fixture level for generated fixtures, or inside request for standalone.
        req = fixture.get("request", {})
        source_mode = fixture.get("source_mode", req.get("source_mode", "web"))
        depth = fixture.get("depth", req.get("depth", "balanced"))
        request = SearchRequest(
            query=fixture["query"],
            mode=mode,
            source_mode=source_mode,
            depth=depth,
            max_iterations=2,
            include_citations=True,
        )
        response = await self.orchestrator.execute_search(request)
        # Trust the orchestrator's real confidence; do not mutate post-execution.
        return response, evaluate_response(fixture, response)

    async def test_fixture_inventory_has_expected_coverage(self):
        self.assertGreaterEqual(len(self.fixtures), 12)
        self.assertLessEqual(len(self.fixtures), 18)

        categories = {fixture.get("category", "unknown") for fixture in self.fixtures}
        # Exclude "standalone" (rich fixtures that don't use fixture-generator schema).
        meaningful = categories - {"standalone", "unknown"}
        # At minimum we expect generated fixtures to span the key buckets.
        for bucket in ("factual lookup", "comparison", "recency-sensitive",
                       "technical how-to", "broad explainer",
                       "contradiction-heavy", "sparse-evidence"):
            self.assertIn(bucket, categories, f"missing bucket: {bucket}; got: {categories}")

    async def test_strong_fixture_passes_quality_gates(self):
        fixture = next(item for item in self.fixtures if item["scenario"] == "strong_research")
        response, result = await self._run_fixture(fixture)

        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(len(response.citations), result["min_citations"])
        self.assertEqual(response.diagnostics.errors, [])
        self.assertIn(response.confidence, {"medium", "high"})

    async def test_negative_fixture_fails_quality_gates(self):
        fixture = next(item for item in self.fixtures if item["scenario"] == "negative_generic")
        response, result = await self._run_fixture(fixture)

        self.assertFalse(result["passed"], result)
        gate_failures = [f for f in result["failures"] if f.startswith("gate:")]
        self.assertGreater(len(gate_failures), 0, "negative fixture should fail at least one gate; got: " + str(result["failures"]))
        self.assertEqual(len(response.citations), 1)

    async def test_all_fixtures_run_through_offline_orchestrator_path(self):
        for fixture in self.fixtures:
            response, result = await self._run_fixture(fixture)
            self.assertEqual(response.query, fixture["query"])
            self.assertTrue(response.summary)
            self.assertIsInstance(result["failures"], list)
            self.assertEqual(response.diagnostics.errors, [])


if __name__ == "__main__":
    unittest.main()
