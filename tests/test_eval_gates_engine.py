from __future__ import annotations

import unittest

from app.models.contracts import Citation, Finding, Source
from app.models.internal import SearchDiagnostics, SearchResponse
from tests.eval_gates import score_response


class EvalGateEngineTests(unittest.TestCase):
    def _response(
        self,
        *,
        query: str = "Compare Docker Compose and Kubernetes for a small self-hosted stack",
        direct_answer: str = "Docker Compose is simpler for a small self-hosted stack, while Kubernetes adds resilience and scaling with more operational overhead.",
        summary: str = (
            "Docker Compose fits a small self-hosted stack when you want a single declarative file and low operational overhead, "
            "while Kubernetes adds scheduling, self-healing, and rolling updates that make more sense once resilience and scaling matter. "
            "The tradeoff is a steeper operational model and more cluster management."
        ),
        findings: list[Finding] | None = None,
        citations: list[Citation] | None = None,
        confidence: str = "high",
    ) -> SearchResponse:
        citations = citations or [
            Citation(
                id=1,
                title="Docker Compose features and use cases",
                url="https://docs.docker.com/compose/intro/features-uses/",
                source="docs.docker.com",
                excerpt="Docker Compose keeps multi-container applications simple with a single declarative file.",
                relevance_score=0.9,
                passage_id="p1",
            ),
            Citation(
                id=2,
                title="Kubernetes overview",
                url="https://kubernetes.io/docs/concepts/overview/",
                source="kubernetes.io",
                excerpt="Kubernetes provides scheduling, self-healing, and rolling updates across clusters.",
                relevance_score=0.88,
                passage_id="p2",
            ),
            Citation(
                id=3,
                title="EKS cost optimization guidance",
                url="https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt.html",
                source="docs.aws.amazon.com",
                excerpt="Managed Kubernetes adds operational overhead and cost tradeoffs for smaller workloads.",
                relevance_score=0.82,
                passage_id="p3",
            ),
        ]
        findings = findings or [
            Finding(claim="Docker Compose keeps a small stack simpler to operate.", citation_ids=[1]),
            Finding(claim="Kubernetes adds resilience features such as scheduling and self-healing.", citation_ids=[2]),
            Finding(claim="Cluster overhead can outweigh benefits for smaller workloads.", citation_ids=[3]),
        ]
        return SearchResponse(
            query=query,
            mode="research",
            direct_answer=direct_answer,
            summary=summary,
            body=direct_answer,
            findings=findings,
            citations=citations,
            sources=[
                Source(title=citation.title, url=citation.url, source=citation.source)
                for citation in citations
            ],
            follow_up_queries=[],
            diagnostics=SearchDiagnostics(),
            timings={},
            confidence=confidence,
        )

    def test_score_response_passes_grounded_comparison_fixture(self):
        response = self._response()
        rules = {
            "fixture_name": "compose_vs_kubernetes",
            "generic_answer": {"min_query_term_overlap": 2},
            "thin_answer": {"min_findings": 3, "min_summary_chars": 120},
            "grounding_citation": {"min_citations": 3, "min_supported_findings": 3},
            "contradiction_handling": {"required": True},
            "unsupported_certainty": {"max_confidence": "high", "require_uncertainty": False},
            "regression_baseline": {
                "baseline": {"min_citations": 3, "min_findings": 3, "min_summary_chars": 120}
            },
        }

        result = score_response(response, rules)

        self.assertTrue(result.passed)
        self.assertEqual(result.fixture_name, "compose_vs_kubernetes")
        self.assertTrue(all(gate.passed for gate in result.gates))

    def test_generic_answer_gate_reports_filler_phrase_reason(self):
        response = self._response(
            query="Compare note-taking apps for students",
            direct_answer="It depends on your needs.",
            summary=(
                "It depends on your needs and there are several factors to consider. "
                "The best choice depends on your workflow and preferences."
            ),
            findings=[Finding(claim="Students should choose what fits best.", citation_ids=[1])],
            confidence="medium",
        )
        rules = {
            "generic_answer": {"min_query_term_overlap": 2},
            "thin_answer": {"min_findings": 2, "min_summary_chars": 90},
            "grounding_citation": {"min_citations": 3, "min_supported_findings": 2},
            "contradiction_handling": {"required": False},
            "unsupported_certainty": {"max_confidence": "medium", "require_uncertainty": False},
            "regression_baseline": {
                "baseline": {"min_citations": 3, "min_findings": 2, "min_summary_chars": 90}
            },
        }

        result = score_response(response, rules)
        generic_gate = next(gate for gate in result.gates if gate.name == "generic_answer")

        self.assertFalse(result.passed)
        self.assertFalse(generic_gate.passed)
        self.assertEqual(generic_gate.reason, "generic filler phrase detected")

    def test_sparse_evidence_fixture_requires_uncertainty_language(self):
        response = self._response(
            query="Are there strong public benchmarks for local-first RAG on regulated internal document sets?",
            direct_answer=(
                "Public benchmarks appear limited, so any answer should stay provisional rather than claiming strong comparative evidence."
            ),
            summary=(
                "Public evidence for local-first RAG on regulated internal document sets looks limited and uneven, so a careful synthesis should say the benchmarks are sparse, "
                "note that methods and document sets vary, and keep conclusions cautious until stronger public comparisons emerge."
            ),
            findings=[
                Finding(claim="Public benchmarks in this niche are limited.", citation_ids=[1]),
                Finding(claim="Available comparisons use different methods and document sets.", citation_ids=[2]),
            ],
            citations=[
                Citation(
                    id=1,
                    title="Local-first RAG evaluation notes",
                    url="https://example.com/local-rag-eval",
                    source="example.com",
                    excerpt="Evidence is limited and benchmarks are not standardized for regulated internal corpora.",
                    relevance_score=0.77,
                    passage_id="p1",
                ),
                Citation(
                    id=2,
                    title="Enterprise retrieval benchmarking caveats",
                    url="https://example.org/enterprise-rag-caveats",
                    source="example.org",
                    excerpt="Different corpora and compliance constraints make direct comparisons uncertain.",
                    relevance_score=0.74,
                    passage_id="p2",
                ),
            ],
            confidence="medium",
        )
        rules = {
            "generic_answer": {"min_query_term_overlap": 1},
            "thin_answer": {"min_findings": 2, "min_summary_chars": 120},
            "grounding_citation": {"min_citations": 2, "min_supported_findings": 2},
            "contradiction_handling": {"required": False},
            "unsupported_certainty": {"max_confidence": "medium", "require_uncertainty": True},
            "regression_baseline": {
                "baseline": {"min_citations": 2, "min_findings": 2, "min_summary_chars": 120}
            },
        }

        result = score_response(response, rules)
        certainty_gate = next(gate for gate in result.gates if gate.name == "unsupported_certainty")

        self.assertTrue(result.passed)
        self.assertTrue(certainty_gate.passed)
        self.assertEqual(certainty_gate.reason, "confidence=medium")

    def test_sparse_evidence_fixture_rejects_overconfident_language(self):
        response = self._response(
            query="Are there strong public benchmarks for local-first RAG on regulated internal document sets?",
            direct_answer="There are definitely strong public benchmarks for this niche.",
            summary=(
                "There are definitely enough benchmarks to draw a clear conclusion, and the best local-first approach is obvious from the public evidence without doubt."
            ),
            findings=[
                Finding(claim="This niche is clearly well benchmarked.", citation_ids=[1]),
                Finding(claim="The best approach is obvious.", citation_ids=[2]),
            ],
            citations=[
                Citation(
                    id=1,
                    title="Limited local-first RAG evaluation notes",
                    url="https://example.com/local-rag-eval",
                    source="example.com",
                    excerpt="Evidence is limited for regulated internal corpora.",
                    relevance_score=0.71,
                    passage_id="p1",
                ),
                Citation(
                    id=2,
                    title="Enterprise retrieval benchmarking caveats",
                    url="https://example.org/enterprise-rag-caveats",
                    source="example.org",
                    excerpt="Comparisons remain uncertain because corpora and compliance constraints vary.",
                    relevance_score=0.69,
                    passage_id="p2",
                ),
            ],
            confidence="medium",
        )
        rules = {
            "generic_answer": {"min_query_term_overlap": 1},
            "thin_answer": {"min_findings": 2, "min_summary_chars": 90},
            "grounding_citation": {"min_citations": 2, "min_supported_findings": 2},
            "contradiction_handling": {"required": False},
            "unsupported_certainty": {"max_confidence": "medium", "require_uncertainty": True},
            "regression_baseline": {
                "baseline": {"min_citations": 2, "min_findings": 2, "min_summary_chars": 90}
            },
        }

        result = score_response(response, rules)
        certainty_gate = next(gate for gate in result.gates if gate.name == "unsupported_certainty")

        self.assertFalse(result.passed)
        self.assertFalse(certainty_gate.passed)
        self.assertEqual(certainty_gate.reason, "missing uncertainty language")


if __name__ == "__main__":
    unittest.main()
