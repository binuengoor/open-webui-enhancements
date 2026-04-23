from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


Mode = Literal["auto", "fast", "deep", "research", "fast_fallback"]
ExecutionMode = Literal["fast", "deep", "research", "fast_fallback"]
SourceMode = Literal["web", "academia", "social", "all"]
DepthMode = Literal["quick", "balanced", "quality"]
ResearchDepthMode = Literal["quick", "balanced", "quality"]
DecisionSource = Literal["heuristic", "llm", "override"]


def _validate_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be a valid http or https URL")
    return value


HttpUrlString = Annotated[str, AfterValidator(_validate_http_url)]


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
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1)
    source_mode: SourceMode = "web"
    depth: ResearchDepthMode = "quality"
    history: List[Dict[str, Any]] = Field(default_factory=list)
    system_instructions: str = ""
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
    search_mode: Optional[Literal["auto", "web", "academic", "sec"]] = Field(
        default=None,
        description="Search surface selector. `auto` is equivalent to omitting the field and uses the default web search behavior.",
    )
    mode: Optional[Mode] = Field(
        default=None,
        description="Deprecated. Endpoint selection should determine behavior: /search for concise results, /research for long-form output.",
    )
    client: Optional[str] = None
    trace_id: Optional[str] = None

    @field_validator("search_mode", mode="before")
    @classmethod
    def normalize_auto_search_mode(cls, value: Optional[str]) -> Optional[str]:
        if value == "auto":
            return None
        return value


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


class SearxngCompatRequest(BaseModel):
    q: str = Field(min_length=1)
    format: str = "json"
    categories: Optional[str] = None
    engines: Optional[str] = None
    language: Optional[str] = None
    pageno: int = Field(default=1, ge=1)
    time_range: Optional[str] = None

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        if value != "json":
            raise ValueError("unsupported format; only json is implemented")
        return value

    @property
    def categories_list(self) -> List[str]:
        return [item.strip().lower() for item in (self.categories or "").split(",") if item.strip()]

    @property
    def engines_list(self) -> List[str]:
        return [item.strip().lower() for item in (self.engines or "").split(",") if item.strip()]


class SearxngCompatResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "Untitled"
    url: str = ""
    content: str = ""
    engine: Optional[str] = None
    img_src: Optional[str] = None
    thumbnail: Optional[str] = None
    iframe_src: Optional[str] = None


class SearxngCompatResponse(BaseModel):
    query: str
    number_of_results: int = 0
    results: List[SearxngCompatResult] = Field(default_factory=list)
    answers: List[Any] = Field(default_factory=list)
    corrections: List[Any] = Field(default_factory=list)
    infoboxes: List[Any] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    unresponsive_engines: List[Any] = Field(default_factory=list)


class FetchRequest(BaseModel):
    url: HttpUrlString


class ExtractRequest(BaseModel):
    url: HttpUrlString
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
