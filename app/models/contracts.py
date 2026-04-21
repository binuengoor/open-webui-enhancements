from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


Mode = Literal["auto", "fast", "deep", "research", "fast_fallback"]
ExecutionMode = Literal["fast", "deep", "research", "fast_fallback"]
SourceMode = Literal["web", "academia", "social", "all"]
DepthMode = Literal["quick", "balanced", "quality"]
DecisionSource = Literal["heuristic", "llm", "override"]


class RoutingDecision(BaseModel):
    requested_mode: Mode
    selected_mode: ExecutionMode
    decision_source: DecisionSource
    reason: str
    profile: Dict[str, bool] = Field(default_factory=dict)


class ResearchPlanStep(BaseModel):
    step: str
    text: str
    purpose: str


class ResearchPlan(BaseModel):
    query: str
    mode: ExecutionMode
    steps: List[ResearchPlanStep, ...] = Field(default_factory=list, max_length=20)
    max_iterations: int = Field(default=1, ge=1, le=8)
    bounded: bool = True


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Mode = "auto"
    source_mode: SourceMode = "web"
    depth: DepthMode = "balanced"
    max_iterations: int = Field(default=4, ge=1, le=8)
    include_citations: bool = True
    include_debug: bool = False
    include_legacy: bool = False
    strict_runtime: bool = False
    user_context: Dict[str, Any] = Field(default_factory=dict)


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1)
    source_mode: SourceMode = "web"
    depth: DepthMode = "quality"
    max_iterations: int = Field(default=4, ge=1, le=8)
    include_debug: bool = False
    include_legacy: bool = False
    strict_runtime: bool = False
    user_context: Dict[str, Any] = Field(default_factory=dict)


class PerplexitySearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: Union[str, List[str]]
    max_results: int = Field(default=10, ge=1, le=20)
    display_server_time: bool = False
    country: Optional[str] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    max_tokens_per_page: Optional[int] = Field(default=None, ge=1, le=100_000)
    search_language_filter: Optional[List[str]] = None
    search_domain_filter: Optional[List[str]] = None
    search_recency_filter: Optional[Literal["hour", "day", "week", "month", "year"]] = None
    search_after_date_filter: Optional[str] = None
    search_before_date_filter: Optional[str] = None
    last_updated_after_filter: Optional[str] = None
    last_updated_before_filter: Optional[str] = None
    search_mode: Optional[Literal["web", "academic", "sec"]] = None
    mode: Optional[Mode] = Field(
        default=None,
        description="Deprecated. Endpoint selection should determine behavior: /search for concise results, /research for long-form output.",
    )
    client: Optional[str] = None
    trace_id: Optional[str] = None


class PerplexitySearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    snippet: str
    date: Optional[str] = None
    last_updated: Optional[str] = None
    citation_ids: Optional[List[int]] = None
    evidence_spans: Optional[List[str]] = None
    confidence: Optional[float] = None
    grounding_notes: Optional[str] = None


class PerplexitySearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    results: List[PerplexitySearchResult]
    server_time: Optional[str] = None


class FetchRequest(BaseModel):
    url: str


class ExtractRequest(BaseModel):
    url: str
    components: str = "all"


class Finding(BaseModel):
    claim: str
    citation_ids: List[int]


class Citation(BaseModel):
    id: int
    title: str
    url: str
    source: str
    excerpt: str
    published_at: str = ""
    last_updated: Optional[str] = None
    language: Optional[str] = None
    relevance_score: float
    passage_id: str


class Source(BaseModel):
    title: str
    url: str
    source: str


class SearchDiagnostics(BaseModel):
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    runtime: Dict[str, Any] = Field(default_factory=dict)
    routing_decision: RoutingDecision | None = None
    research_plan: ResearchPlan | None = None
    query_plan: List[Dict[str, str]] = Field(default_factory=list)
    iterations: int = 1
    coverage_notes: List[str] = Field(default_factory=list)
    search_count: int = 0
    fetched_count: int = 0
    ranked_passage_count: int = 0
    provider_trace: List[Dict[str, Any]] = Field(default_factory=list)
    cache: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    mode: Mode
    direct_answer: str
    summary: str
    findings: List[Finding]
    citations: List[Citation]
    sources: List[Source]
    follow_up_queries: List[str]
    diagnostics: SearchDiagnostics
    timings: Dict[str, int]
    confidence: Literal["low", "medium", "high"]
    legacy: Optional[Dict[str, Any]] = None


class ProviderHealthRecord(BaseModel):
    name: str
    enabled: bool
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_failure_reason: str = ""
    # Empty results are tracked separately — they don't count toward cooldown
    # but signal when a provider may be returning no content for queries.
    consecutive_empty_results: int = 0


class ProviderResult(BaseModel):
    url: str
    title: str
    snippet: str
    source: str
    provider: str
    rank: int
