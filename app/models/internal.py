from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.contracts import Citation, ExecutionMode, Finding, Mode, Source


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
    steps: List[ResearchPlanStep] = Field(default_factory=list, max_length=20)
    max_iterations: int = Field(default=1, ge=1, le=8)
    bounded: bool = True


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
    synthesis: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    mode: Mode
    direct_answer: str
    summary: str
    body: str
    findings: List[Finding]
    citations: List[Citation]
    sources: List[Source]
    follow_up_queries: List[str]
    diagnostics: SearchDiagnostics
    timings: Dict[str, int]
    confidence: Literal["low", "medium", "high"]
    legacy: Optional[Dict[str, Any]] = None


class RunHistoryEntry(BaseModel):
    timestamp: str
    endpoint: Literal["/search", "/research"]
    query: str
    mode: ExecutionMode
    success: bool
    citations_count: int = 0
    sources_count: int = 0
    confidence: Optional[Literal["low", "medium", "high"]] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ProgressEvent(BaseModel):
    type: Literal["progress", "complete", "error"]
    state: Literal[
        "search_started",
        "evidence_gathering",
        "vane",
        "vetting",
        "fallback",
        "synthesizing",
        "complete",
        "error",
    ]
    request_id: str
    mode: ExecutionMode
    message: Optional[str] = None
    cycle: Optional[int] = None
    total_cycles: Optional[int] = None
    timings: Optional[Dict[str, int]] = None
    error: Optional[str] = None


class ProviderHealthRecord(BaseModel):
    name: str
    enabled: bool
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_failure_reason: str = ""
    last_failure_type: str = ""
    last_cooldown_seconds: int = 0
    consecutive_empty_results: int = 0
