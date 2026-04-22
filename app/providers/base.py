from __future__ import annotations

from typing import Any, Dict, List


class ProviderError(Exception):
    failure_type = "provider_error"
    immediate_cooldown = False
    cooldown_multiplier = 1.0

    def __init__(self, message: str, *, cooldown_seconds: int | None = None):
        super().__init__(message)
        self.cooldown_seconds = cooldown_seconds


class RateLimitError(ProviderError):
    failure_type = "rate_limit"
    immediate_cooldown = True


class AuthProviderError(ProviderError):
    failure_type = "auth"
    immediate_cooldown = True
    cooldown_multiplier = 3.0


class UpstreamProviderError(ProviderError):
    failure_type = "upstream"
    immediate_cooldown = True
    cooldown_multiplier = 1.5


class TransientProviderError(ProviderError):
    failure_type = "transient"
    immediate_cooldown = False


class SearchProvider:
    name: str

    async def search(self, query: str, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError
