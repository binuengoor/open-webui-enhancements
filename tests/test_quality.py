from __future__ import annotations

import unittest

from app.eval.quality import looks_useful_search_response


class QualityResponseTests(unittest.TestCase):
    def _payload(self, **overrides):
        payload = {
            "query": "Are there strong public benchmarks for local-first RAG on regulated internal document sets?",
            "mode": "research",
            "direct_answer": (
                "Public evidence appears limited, so any conclusion should stay provisional rather than claiming a settled answer."
            ),
            "summary": (
                "Public evidence for local-first RAG on regulated internal document sets appears limited and uneven, "
                "so a grounded answer should stay cautious, note that methods vary, and avoid overclaiming until stronger public comparisons emerge."
            ),
            "findings": [
                {"claim": "Public benchmarks in this niche are limited.", "citation_ids": [1]},
                {"claim": "Available comparisons use different methods and document sets.", "citation_ids": [2]},
            ],
            "citations": [
                {"id": 1, "title": "Local-first RAG evaluation notes", "url": "https://example.com/a"},
                {"id": 2, "title": "Enterprise retrieval benchmarking caveats", "url": "https://example.com/b"},
            ],
            "sources": [
                {"title": "Local-first RAG evaluation notes", "url": "https://example.com/a", "source": "example.com"},
                {"title": "Enterprise retrieval benchmarking caveats", "url": "https://example.com/b", "source": "example.com"},
            ],
            "diagnostics": {"errors": [], "warnings": []},
            "confidence": "medium",
        }
        payload.update(overrides)
        return payload

    def test_accepts_strong_standard_research_payload(self):
        payload = self._payload(
            confidence="high",
            citations=[
                {"id": 1, "title": "A", "url": "https://example.com/a"},
                {"id": 2, "title": "B", "url": "https://example.com/b"},
                {"id": 3, "title": "C", "url": "https://example.com/c"},
            ],
            sources=[
                {"title": "A", "url": "https://example.com/a", "source": "example.com"},
                {"title": "B", "url": "https://example.com/b", "source": "example.org"},
                {"title": "C", "url": "https://example.com/c", "source": "example.net"},
            ],
        )

        self.assertTrue(looks_useful_search_response(payload))

    def test_accepts_sparse_but_grounded_research_payload(self):
        self.assertTrue(looks_useful_search_response(self._payload()))

    def test_accepts_grounded_fast_payload_with_two_sources(self):
        payload = self._payload(
            mode="fast",
            query="latest postgres 17 replication slots changes",
            direct_answer=(
                "PostgreSQL 17 adds failover slot synchronization details and related replication slot management updates in the release notes and docs."
            ),
            summary=(
                "PostgreSQL 17 documentation and release notes both describe replication slot updates, including failover slot synchronization details and operational caveats, "
                "which is enough for a concise grounded /search response without requiring a full long-form synthesis."
            ),
            findings=[
                {"claim": "PostgreSQL 17 documents replication slot management updates.", "citation_ids": [1]},
            ],
            confidence="high",
        )

        self.assertTrue(looks_useful_search_response(payload))

    def test_rejects_fast_payload_with_only_one_finding_and_one_source(self):
        payload = self._payload(
            mode="fast",
            findings=[{"claim": "A single vague claim.", "citation_ids": [1]}],
            citations=[{"id": 1, "title": "A", "url": "https://example.com/a"}],
            sources=[{"title": "A", "url": "https://example.com/a", "source": "example.com"}],
            direct_answer="This seems relevant.",
            summary="A short answer with too little evidence.",
            confidence="high",
        )

        self.assertFalse(looks_useful_search_response(payload))

    def test_rejects_sparse_payload_with_low_confidence(self):
        payload = self._payload(confidence="low")

        self.assertFalse(looks_useful_search_response(payload))

    def test_rejects_sparse_payload_with_thin_summary(self):
        payload = self._payload(summary="Evidence is limited.")

        self.assertFalse(looks_useful_search_response(payload))


if __name__ == "__main__":
    unittest.main()
