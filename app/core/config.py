from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8091
    request_timeout_s: int = 25


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
    chat_provider_id_env: str = "VANE_CHAT_PROVIDER_ID"
    chat_model_key: str = "auto-main"
    embedding_provider_id_env: str = "VANE_EMBED_PROVIDER_ID"
    embedding_model_key: str = "Xenova/nomic-embed-text-v1"


class CompilerConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    timeout_s: int = 20
    model_id: str = ""


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
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.sample.yaml"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _csv_set(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


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
        "FLARESOLVERR_URL",
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
    payload["vane"]["chat_model_key"] = _env(
        "VANE_CHAT_MODEL_KEY",
        payload["vane"].get("chat_model_key", "auto-main"),
    )
    payload["vane"]["embedding_model_key"] = _env(
        "VANE_EMBED_MODEL_KEY",
        payload["vane"].get("embedding_model_key", "Xenova/nomic-embed-text-v1"),
    )

    payload.setdefault("compiler", {})
    payload["compiler"]["enabled"] = _env(
        "EWS_COMPILER_ENABLED",
        str(payload["compiler"].get("enabled", False)),
    ).lower() in {"1", "true", "yes", "on"}
    payload["compiler"]["base_url"] = _env(
        "EWS_COMPILER_BASE_URL",
        payload["compiler"].get("base_url", _env("LITELLM_SEARCH_BASE_URL", "")),
    )
    payload["compiler"]["timeout_s"] = int(
        _env("EWS_COMPILER_TIMEOUT", str(payload["compiler"].get("timeout_s", 20)))
    )
    payload["compiler"]["model_id"] = _env(
        "EWS_COMPILER_MODEL_ID",
        payload["compiler"].get("model_id", ""),
    )

    shared_litellm_key_env = "LITELLM_API_KEY"
    allowed_litellm = _csv_set(_env("LITELLM_ENABLED_PROVIDERS", ""))
    providers = payload.get("providers") or []
    for provider in providers:
        if provider.get("kind") != "litellm-search":
            continue
        provider["api_key_env"] = shared_litellm_key_env
        if allowed_litellm:
            provider["enabled"] = provider.get("name", "").strip().lower() in allowed_litellm

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
