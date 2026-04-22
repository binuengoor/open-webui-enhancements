from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from app.cache.memory_cache import InMemoryCache
from app.services.orchestrator import ResearchOrchestrator
from app.models.contracts import SearchRequest
from app.services.planner import QueryPlanner
from app.services.ranking import Ranker


class _StubRouter:
    def health_snapshot(self):
        return []


class _StubFetcher:
    async def fetch(self, url):
        return {"url": url}

    async def extract(self, url):
        return {"url": url}


class _StubVane:
    def __init__(self, response=None):
        self.response = response or {"error": "disabled"}

    async def deep_search(self, query, source_mode, depth):
        return self.response


class _StubCompiler:
    def __init__(self, vet_response=None, vane_summary_response=None):
        self.vet_response = vet_response
        self.vane_summary_response = vane_summary_response

    async def choose_search_profile(self, **kwargs):
        return None

    async def compile_perplexity_results(self, **kwargs):
        return None

    async def vet_research_response(self, **kwargs):
        return self.vet_response

    async def summarize_vane_content(self, **kwargs):
        return self.vane_summary_response


class ResearchOutputQualityTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            modes={
                "fast": SimpleNamespace(max_provider_attempts=1, max_queries=1, max_pages_to_fetch=4),
                "deep": SimpleNamespace(max_provider_attempts=1, max_queries=1, max_pages_to_fetch=4),
                "research": SimpleNamespace(max_provider_attempts=1, max_queries=2, max_pages_to_fetch=4),
                "fast_fallback": SimpleNamespace(max_provider_attempts=1, max_queries=1, max_pages_to_fetch=3),
            },
            vane=SimpleNamespace(enabled=False, default_optimization_mode="balanced"),
            planner=SimpleNamespace(llm_fallback_enabled=False),
            cache=SimpleNamespace(ttl_general_s=300, ttl_recency_s=45, page_cache_ttl_s=120),
            research_llm_ready=False,
            research_llm_requirement_error="research mode requires LLM support",
        )
        self.orchestrator = self._make_orchestrator()

    def _make_orchestrator(self, vane_response=None, compiler=None):
        return ResearchOrchestrator(
            config=self.config,
            router=_StubRouter(),
            search_cache=InMemoryCache(max_entries=8),
            page_cache=InMemoryCache(max_entries=8),
            fetcher=_StubFetcher(),
            planner=QueryPlanner(),
            ranker=Ranker(),
            vane=_StubVane(vane_response),
            compiler=compiler or _StubCompiler(),
        )

    def test_ensure_research_llm_available_rejects_missing_llm(self):
        with self.assertRaisesRegex(ValueError, "research mode requires LLM support"):
            self.orchestrator.ensure_research_llm_available()

    def test_ensure_research_llm_available_accepts_when_ready(self):
        self.config.research_llm_ready = True

        self.orchestrator.ensure_research_llm_available()

    def test_best_excerpt_skips_boilerplate_and_fragment_lines(self):
        content = """Context.
Please refer to the appropriate style manual for citation rules.
Skip to main content.
Black holes grow when gas falls into the accretion disk, heats up, and emits X-rays that astronomers can measure.
."""

        excerpt = self.orchestrator._best_excerpt(content, "How do black holes grow?")

        self.assertIn("accretion disk", excerpt)
        self.assertNotIn("style manual", excerpt.lower())
        self.assertNotEqual(excerpt, "Context.")

    def test_build_findings_filters_junk_claims(self):
        citations = [
            {"id": 1, "title": "Context", "excerpt": "Context.", "source": "a.example", "relevance_score": 0.9},
            {
                "id": 2,
                "title": "Black hole growth observed",
                "excerpt": "Black holes can gain mass by accreting nearby gas, and the infalling material heats up enough to produce observable radiation.",
                "source": "b.example",
                "relevance_score": 0.8,
            },
            {
                "id": 3,
                "title": "Accretion evidence",
                "excerpt": "Astronomers estimate growth rates by combining telescope measurements of radiation from accreting material with models of the surrounding environment.",
                "source": "c.example",
                "relevance_score": 0.7,
            },
        ]

        findings = self.orchestrator._build_findings("How do black holes grow?", citations, "research")

        self.assertGreaterEqual(len(findings), 1)
        joined = " ".join(item["claim"] for item in findings)
        self.assertIn("Black holes can gain mass", joined)
        self.assertNotIn("Context.", joined)

    def test_build_findings_rejects_title_like_and_off_topic_snippets(self):
        citations = [
            {
                "id": 1,
                "title": "How Do We Know There Are Black Holes?",
                "excerpt": "How Do We Know There Are Black Holes?",
                "source": "a.example",
                "relevance_score": 0.95,
            },
            {
                "id": 2,
                "title": "Dark energy survey update",
                "excerpt": "DESI mapped millions of galaxies to measure dark energy and the expansion history of the universe.",
                "source": "b.example",
                "relevance_score": 0.9,
            },
            {
                "id": 3,
                "title": "Black hole accretion evidence",
                "excerpt": "Black holes grow primarily by accreting nearby gas and dust, converting gravitational energy into radiation that telescopes can detect.",
                "source": "c.example",
                "relevance_score": 0.8,
            },
        ]

        findings = self.orchestrator._build_findings("How do black holes grow?", citations, "research")

        self.assertEqual(len(findings), 1)
        self.assertIn("Black holes grow primarily by accreting nearby gas and dust", findings[0]["claim"])
        self.assertNotIn("How Do We Know", findings[0]["claim"])
        self.assertNotIn("dark energy", findings[0]["claim"].lower())

    def test_build_citations_drops_weak_overlap_pages(self):
        pages = [
            {
                "url": "https://a.example/black-holes",
                "title": "Black hole growth observed",
                "content": "Black holes grow when nearby gas falls inward, forms an accretion disk, and radiates energy that astronomers can measure.",
                "snippet": "Black holes grow when nearby gas falls inward.",
                "quality_score": 0.75,
            },
            {
                "url": "https://b.example/desi",
                "title": "DESI update",
                "content": "The DESI survey measures dark energy by mapping galaxies across cosmic time.",
                "snippet": "The DESI survey measures dark energy.",
                "quality_score": 0.7,
            },
        ]

        citations = self.orchestrator._build_citations("How do black holes grow?", pages)

        self.assertEqual(len(citations), 1)
        self.assertIn("accretion disk", citations[0]["excerpt"])

    def test_condense_vane_text_preserves_paragraph_structure(self):
        text = """Overview

Black holes primarily grow through accretion, where nearby gas spirals inward and converts gravitational energy into radiation.

Mergers can also increase mass, especially in galaxy collisions where two black holes eventually coalesce after losing orbital energy."""

        condensed = self.orchestrator._condense_vane_text(text, max_len=500, max_sentences=4, preserve_structure=True)

        self.assertIn("\n\n", condensed)
        self.assertIn("accretion", condensed.lower())
        self.assertIn("mergers", condensed.lower())

    def test_planner_decomposes_compound_black_hole_query(self):
        query = "how does blackholes form, what is the most significant discovery related to blackholes in the past year?"

        steps = self.orchestrator.planner.initial_plan(query, "research")
        texts = [step["text"] for step in steps]

        self.assertIn(query, texts)
        self.assertIn("how does blackholes form?", texts)
        self.assertIn("what is the most significant discovery related to blackholes in the past year?", texts)

    def test_followup_query_prefers_uncovered_subquestion(self):
        query = "how does blackholes form, what is the most significant discovery related to blackholes in the past year?"

        followup = self.orchestrator.planner.followup_query(
            query,
            ["Black Holes - NASA Science", "What is a black hole? | University of Chicago News"],
        )

        self.assertEqual(
            followup,
            "what is the most significant discovery related to blackholes in the past year?",
        )

    def test_research_answer_is_structured_by_subquestion(self):
        query = "how does blackholes form, what is the most significant discovery related to blackholes in the past year?"
        findings = [
            {
                "claim": "Black holes form when very massive stars collapse after exhausting their fuel, and they can also grow through accretion and mergers.",
                "citation_ids": [1, 2],
            },
            {
                "claim": "A notable recent result is evidence for an early supermassive black hole that appears to have formed before most of its host galaxy, challenging standard growth timelines.",
                "citation_ids": [3, 4],
            },
            {
                "claim": "This matters because it suggests some supermassive black holes assembled far faster than many standard models expected in the early universe.",
                "citation_ids": [4],
            },
        ]
        citations = [
            {"id": 1, "title": "Formation", "url": "https://a.example", "source": "a.example", "excerpt": findings[0]["claim"], "relevance_score": 0.8},
            {"id": 2, "title": "Growth", "url": "https://b.example", "source": "b.example", "excerpt": findings[0]["claim"], "relevance_score": 0.7},
            {"id": 3, "title": "Discovery", "url": "https://c.example", "source": "c.example", "excerpt": findings[1]["claim"], "relevance_score": 0.8},
            {"id": 4, "title": "Early SMBH", "url": "https://d.example", "source": "d.example", "excerpt": findings[1]["claim"], "relevance_score": 0.75},
        ]

        direct_answer = self.orchestrator._build_direct_answer(query, findings, "", "research")
        summary = self.orchestrator._build_summary(query, findings, citations, "research")

        self.assertIn("how does blackholes form:", direct_answer.lower())
        self.assertIn("what is the most significant discovery related to blackholes in the past year:", direct_answer.lower())
        self.assertIn("Supported by 4 citations across 4 sources.", summary)
        self.assertIn("how does blackholes form:", summary.lower())
        self.assertGreater(len(direct_answer), 350)

    def test_coverage_check_adds_missing_subquestion_finding(self):
        query = "how does blackholes form, what is the most significant discovery related to blackholes in the past year?"
        findings = [
            {
                "claim": "A notable recent result is evidence for an early supermassive black hole that appears to have formed before most of its host galaxy, challenging standard growth timelines.",
                "citation_ids": [3, 4],
            }
        ]
        citations = [
            {
                "id": 1,
                "title": "Formation",
                "url": "https://a.example",
                "source": "a.example",
                "excerpt": "Black holes form when massive stars run out of fuel, their cores collapse under gravity, and the remnant becomes dense enough that not even light can escape.",
                "relevance_score": 0.82,
            },
            {
                "id": 3,
                "title": "Discovery",
                "url": "https://c.example",
                "source": "c.example",
                "excerpt": findings[0]["claim"],
                "relevance_score": 0.8,
            },
            {
                "id": 4,
                "title": "Early SMBH",
                "url": "https://d.example",
                "source": "d.example",
                "excerpt": "The object appears to have formed before most of its host galaxy, making it a notable recent black-hole discovery.",
                "relevance_score": 0.75,
            },
        ]

        augmented, notes = self.orchestrator._ensure_query_coverage(query, findings, citations, "research")
        direct_answer = self.orchestrator._build_direct_answer(query, augmented, "", "research")

        self.assertGreaterEqual(len(augmented), 2)
        self.assertTrue(any(note.startswith("supplemented:how does blackholes form?") for note in notes))
        self.assertIn("how does blackholes form:", direct_answer.lower())

    def test_cluster_citations_groups_shared_theme_before_singletons(self):
        citations = [
            {
                "id": 1,
                "title": "Early black hole discovery",
                "excerpt": "A recent black hole discovery reports an early supermassive black hole in the young universe.",
                "source": "a.example",
                "relevance_score": 0.82,
            },
            {
                "id": 2,
                "title": "Another early black hole result",
                "excerpt": "Researchers found another early supermassive black hole, reinforcing the recent discovery theme.",
                "source": "b.example",
                "relevance_score": 0.79,
            },
            {
                "id": 3,
                "title": "Formation background",
                "excerpt": "Black holes form when massive stars collapse after exhausting nuclear fuel.",
                "source": "c.example",
                "relevance_score": 0.7,
            },
        ]

        clusters = self.orchestrator.ranker.cluster_citations(
            "how does blackholes form, what is the most significant discovery related to blackholes in the past year?",
            citations,
        )

        self.assertGreaterEqual(len(clusters[0]), 2)

    def test_merge_vane_synthesis_preserves_substantive_body(self):
        vane_answer = (
            "Black holes grow mainly through accretion, where surrounding gas spirals inward, heats up, and emits radiation that lets astronomers estimate how quickly mass is being added.\n\n"
            "They can also gain mass through mergers, especially after galaxy collisions, and the balance between accretion and mergers depends on the environment and cosmic era.\n\n"
            "Recent observations combine X-ray, radio, and infrared evidence to compare those growth channels rather than treating them as a single process."
        )

        body, direct_answer, summary, decision = asyncio.run(
            self.orchestrator._merge_vane_synthesis(
                query="How do black holes grow?",
                deep_synthesis={"answer": vane_answer, "summary": "Black holes grow through accretion and mergers."},
                direct_answer="local answer",
                summary="local summary",
                body="local body",
                mode="research",
            )
        )

        self.assertEqual(body, vane_answer)
        self.assertNotEqual(direct_answer, "local answer")
        self.assertLess(len(direct_answer), len(vane_answer))
        self.assertTrue(summary)
        self.assertLessEqual(len(summary), len(direct_answer))
        self.assertFalse(vane_answer.startswith(direct_answer))
        self.assertFalse(direct_answer.startswith(summary))
        self.assertTrue(decision["accepted"])
        self.assertTrue(decision["body_preserved"])
        self.assertEqual(decision["body_source"], "vane_answer")

    def test_merge_vane_synthesis_rejects_filler_answer(self):
        body, direct_answer, summary, decision = asyncio.run(
            self.orchestrator._merge_vane_synthesis(
                query="How do black holes grow?",
                deep_synthesis={"answer": "I was not able to find the information.", "summary": "No results found."},
                direct_answer="local answer",
                summary="local summary",
                body="local body",
                mode="research",
            )
        )

        self.assertEqual(body, "local body")
        self.assertEqual(direct_answer, "local answer")
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["rejection_reason"], "filler_phrase")

    def test_merge_vane_synthesis_uses_llm_shaping_when_available(self):
        orchestrator = self._make_orchestrator(
            compiler=_StubCompiler(
                vane_summary_response={
                    "direct_answer": "Black holes mostly grow by accreting gas, with mergers adding mass in some environments.",
                    "summary": "Growth comes mainly from accretion, with mergers as a secondary channel.",
                    "direct_source": "llm_summary",
                    "summary_source": "llm_summary",
                }
            )
        )
        vane_answer = (
            "Black holes grow mainly through accretion, where surrounding gas spirals inward, heats up, and emits radiation that lets astronomers estimate how quickly mass is being added. "
            "They can also gain mass through mergers after galaxy collisions, and the balance depends on environment and cosmic era. "
            "Recent observations compare those channels across multiple wavelengths."
        )

        body, direct_answer, summary, decision = asyncio.run(
            orchestrator._merge_vane_synthesis(
                query="How do black holes grow?",
                deep_synthesis={"answer": vane_answer, "summary": "Black holes grow through accretion and mergers."},
                direct_answer="local answer",
                summary="local summary",
                body="local body",
                mode="research",
            )
        )

        self.assertEqual(body, vane_answer)
        self.assertEqual(decision["direct_answer_source"], "llm_summary")
        self.assertEqual(decision["summary_source"], "llm_summary")
        self.assertFalse(vane_answer.startswith(direct_answer))
        self.assertFalse(direct_answer.startswith(summary))

    def test_merge_vane_synthesis_reuses_good_vane_summary_when_llm_unavailable(self):
        vane_answer = (
            "Black holes gain mass through long periods of gas accretion and through mergers, with observations using emitted radiation and host-galaxy context to estimate which channel dominates.\n\n"
            "The mix changes with environment, so modern surveys compare multiple wavelengths and populations rather than assuming one growth path."
        )
        vane_summary = "Black holes usually grow by accreting gas, while mergers matter more in some collision-rich environments."

        body, direct_answer, summary, decision = asyncio.run(
            self.orchestrator._merge_vane_synthesis(
                query="How do black holes grow?",
                deep_synthesis={"answer": vane_answer, "summary": vane_summary},
                direct_answer="local answer",
                summary="local summary",
                body="local body",
                mode="research",
            )
        )

        self.assertEqual(body, vane_answer)
        self.assertEqual(direct_answer, vane_summary)
        self.assertEqual(decision["direct_answer_source"], "vane_summary")
        self.assertFalse(direct_answer.startswith(summary))

    def test_accepted_vane_payload_skips_fallback_even_if_compact_gates_disagree(self):
        payload = {
            "query": "black hole growth",
            "mode": "research",
            "direct_answer": "Long grounded body.",
            "summary": "Short summary.",
            "body": "Long grounded body.",
            "findings": [],
            "citations": [],
            "sources": [],
            "follow_up_queries": [],
            "diagnostics": {"warnings": [], "errors": [], "coverage_notes": [], "provider_trace": [], "synthesis": {"vane": {"accepted": True}}},
            "timings": {"total_ms": 1},
            "confidence": "low",
        }

        result = asyncio.run(
            self.orchestrator._maybe_vet_and_fallback_research(
                req=SearchRequest(query="black hole growth", mode="research"),
                payload=payload,
                request_id="abc123",
                started=0.0,
                selected_mode="research",
                mode_budget=self.config.modes["research"],
            )
        )

        self.assertIsNone(result)

    def test_fallback_payload_marks_body_source_and_body_field(self):
        payload = {
            "query": "black hole growth",
            "mode": "research",
            "direct_answer": "local",
            "summary": "local summary",
            "body": "local",
            "findings": [],
            "citations": [],
            "sources": [],
            "follow_up_queries": [],
            "diagnostics": {"warnings": [], "errors": [], "coverage_notes": [], "provider_trace": [], "synthesis": {"vane": {"accepted": False}}},
            "timings": {"total_ms": 1},
            "confidence": "low",
        }

        fallback = self.orchestrator._research_payload_from_perplexity(
            req=SearchRequest(query="black hole growth", mode="research"),
            original_payload=payload,
            fallback_response=SimpleNamespace(
                results=[
                    SimpleNamespace(
                        title="Black hole growth observed",
                        url="https://example.com/growth",
                        snippet="Black holes grow by accreting nearby gas and by merging with other black holes.",
                        last_updated=None,
                        confidence=0.8,
                    )
                ]
            ),
            request_id="abc123",
            started=0.0,
            fallback_query="black hole growth observed",
        )

        self.assertEqual(fallback["body"], fallback["direct_answer"])
        self.assertTrue(fallback["diagnostics"]["synthesis"]["fallback_used"])
        self.assertEqual(fallback["diagnostics"]["synthesis"]["body_source"], "fallback_synthesis")

    def test_execute_search_preserves_vane_body_and_stream_progress(self):
        vane_answer = (
            "Black holes grow through accretion and mergers. Observers estimate accretion rates by measuring radiation from infalling matter across multiple wavelengths.\n\n"
            "Recent work compares environments, showing that dense gas supply, feedback, and galaxy interactions can all change which growth channel dominates.\n\n"
            "That makes growth histories context dependent rather than a single universal sequence."
        )
        orchestrator = self._make_orchestrator(vane_response={"answer": vane_answer, "summary": "Accretion and mergers both matter."})

        async def fake_search_once(*args, **kwargs):
            return ([{"url": "https://example.com/a", "title": "A", "snippet": "snippet"}], [], {"hits": 0, "misses": 1})

        async def fake_fetch_pages(query, rows):
            return [
                {
                    "url": "https://example.com/a",
                    "title": "Growth",
                    "content": "Black holes grow through accretion and mergers with observational support from multiple instruments.",
                    "snippet": "Black holes grow through accretion and mergers.",
                    "quality_score": 0.8,
                },
                {
                    "url": "https://example.com/b",
                    "title": "Growth evidence",
                    "content": "Accretion rates can be inferred from emitted radiation across wavelengths.",
                    "snippet": "Accretion rates can be inferred from emitted radiation.",
                    "quality_score": 0.7,
                },
                {
                    "url": "https://example.com/c",
                    "title": "Mergers",
                    "content": "Mergers in galaxy collisions also build black hole mass.",
                    "snippet": "Mergers also build black hole mass.",
                    "quality_score": 0.7,
                },
            ]

        orchestrator._search_once = fake_search_once
        orchestrator._fetch_pages = fake_fetch_pages

        events = []

        async def on_progress(event):
            events.append(event.state)

        response = asyncio.run(
            orchestrator.execute_search(
                SearchRequest(query="how do black holes grow?", mode="research", include_citations=True),
                progress_callback=on_progress,
            )
        )

        self.assertEqual(response.body, vane_answer)
        self.assertLess(len(response.direct_answer), len(vane_answer))
        self.assertLessEqual(len(response.summary), len(response.direct_answer))
        self.assertFalse(vane_answer.startswith(response.direct_answer))
        self.assertFalse(response.direct_answer.startswith(response.summary))
        self.assertTrue(response.diagnostics.synthesis["vane"]["body_preserved"])
        self.assertIn(response.diagnostics.synthesis["vane"]["direct_answer_source"], {"vane_summary", "llm_summary", "fallback_truncation"})
        self.assertIn(response.diagnostics.synthesis["vane"]["summary_source"], {"vane_summary", "llm_summary", "fallback_vane_summary", "fallback_truncation"})
        self.assertIn("vane", events)
        self.assertIn("complete", events)


if __name__ == "__main__":
    unittest.main()
