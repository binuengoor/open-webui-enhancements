from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, model_validator


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8091
    request_timeout_s: int = 25
    auto_export_research: bool = False
    report_output_dir: str = "artifacts/reports"


class ModeBudget(BaseModel):
    max_provider_attempts: int
    max_queries: int
    max_pages_to_fetch: int


class RoutingConfig(BaseModel):
    policy: str = "rotating_weighted"
    cooldown_seconds: int = 60
    failure_threshold: int = 2


class ProviderEntry(BaseModel):
    name: str
    kind: str
    enabled: bool = True
    weight: int = 1
    timeout_s: int = 12
    base_url: str = ""
    path: str = ""
    api_key_env: Optional[str] = None
    litellm_provider: Optional[str] = None


class CacheConfig(BaseModel):
    enabled: bool = True
    max_entries: int = 1000
    ttl_general_s: int = 300
    ttl_recency_s: int = 45
    page_cache_ttl_s: int = 120


class ScrapingConfig(BaseModel):
    user_agent: str = "EnhancedWebsearchService/1.0"
    request_timeout_s: int = 18
    max_content_chars: int = 30000
    min_content_chars: int = 80
    flaresolverr_url: str = ""


class VaneConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    timeout_s: int = 25
    default_optimization_mode: str = "balanced"
    chat_provider_id: str = ""
    chat_model_key: str = "auto-main"
    embedding_provider_id: str = ""
    embedding_model_key: str = "Xenova/nomic-embed-text-v1"


class CompilerConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    timeout_s: int = 20
    model_id: str = ""


class PlannerConfig(BaseModel):
    llm_fallback_enabled: bool = False


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json: bool = False


class AppConfig(BaseModel):
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    modes: Dict[str, ModeBudget]
    providers: List[ProviderEntry]
    cache: CacheConfig = Field(default_factory=CacheConfig)
    scraping: ScrapingConfig = Field(default_factory=ScrapingConfig)
    vane: VaneConfig = Field(default_factory=VaneConfig)
    compiler: CompilerConfig = Field(default_factory=CompilerConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def research_llm_ready(self) -> bool:
        return bool(
            self.vane.enabled
            and self.vane.url
            and self.vane.chat_provider_id
            and self.vane.chat_model_key
            and self.vane.embedding_provider_id
            and self.vane.embedding_model_key
        )

    @property
    def research_llm_requirement_error(self) -> str:
        return (
            "research mode requires Vane proxy configuration "
            "(set `VANE_ENABLED=true` with `VANE_URL`, `VANE_CHAT_PROVIDER_ID`, and `VANE_EMBED_PROVIDER_ID`)"
        )

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _env_bool_optional(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _provider_env_flag_name(provider_name: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", provider_name.strip().upper()).strip("_")
    return f"EWS_PROVIDER_{token}_ENABLED"


def _expand_env_placeholders(value):
    if isinstance(value, dict):
        return {k: _expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(v) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value.strip())
        if match:
            return _env(match.group(1), "")
    return value


def _apply_env_overrides(payload: dict) -> dict:
    payload = _expand_env_placeholders(payload)
    payload.setdefault("service", {})
    payload["service"]["host"] = _env("EWS_HOST", payload["service"].get("host", "0.0.0.0"))
    payload["service"]["port"] = int(_env("EWS_PORT", str(payload["service"].get("port", 8091))))

    payload.setdefault("scraping", {})
    payload["scraping"]["flaresolverr_url"] = _env(
        "EWS_FLARESOLVERR_URL",
        payload["scraping"].get("flaresolverr_url", ""),
    )

    payload.setdefault("vane", {})
    payload["vane"]["enabled"] = _env("VANE_ENABLED", str(payload["vane"].get("enabled", False))).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    payload["vane"]["url"] = _env("VANE_URL", payload["vane"].get("url", ""))
    payload["vane"]["default_optimization_mode"] = _env(
        "VANE_DEFAULT_MODE",
        payload["vane"].get("default_optimization_mode", "balanced"),
    )
    payload["vane"]["chat_provider_id"] = _env(
        "VANE_CHAT_PROVIDER_ID",
        payload["vane"].get("chat_provider_id", ""),
    )
    payload["vane"]["chat_model_key"] = _env(
        "VANE_CHAT_MODEL_KEY",
        payload["vane"].get("chat_model_key", "auto-main"),
    )
    payload["vane"]["embedding_provider_id"] = _env(
        "VANE_EMBED_PROVIDER_ID",
        payload["vane"].get("embedding_provider_id", ""),
    )
    payload["vane"]["embedding_model_key"] = _env(
        "VANE_EMBED_MODEL_KEY",
        payload["vane"].get("embedding_model_key", "Xenova/nomic-embed-text-v1"),
    )

    shared_litellm_key_env = "EWS_LITELLM_API_KEY"
    providers = payload.get("providers") or []
    for provider in providers:
        name = provider.get("name", "").strip()
        explicit_enabled = _env_bool_optional(_provider_env_flag_name(name))

        if provider.get("kind") == "litellm-search":
            provider["api_key_env"] = provider.get("api_key_env") or shared_litellm_key_env
            if not provider.get("path") and provider.get("litellm_provider"):
                provider["path"] = f"/search/{provider['litellm_provider']}"

        if explicit_enabled is not None:
            provider["enabled"] = explicit_enabled

    return payload


def load_config(config_path: Optional[str] = None) -> AppConfig:
    path = Path(config_path or _env("EWS_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH)))
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    payload = _apply_env_overrides(payload)
    return AppConfig.model_validate(payload)


def redacted_config(config: AppConfig) -> dict:
    data = config.model_dump()
    for provider in data.get("providers", []):
        if provider.get("api_key_env"):
            provider["api_key_env"] = f"{provider['api_key_env']} (set={bool(_env(provider['api_key_env']))})"
    return data
