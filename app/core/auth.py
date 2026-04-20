from __future__ import annotations

from typing import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class OptionalBearerTokenMiddleware:
    def __init__(self, app, bearer_token: str = "", exempt_paths: Iterable[str] | None = None):
        self.app = app
        self.bearer_token = bearer_token.strip()
        self.exempt_paths = {path.rstrip("/") or "/" for path in (exempt_paths or [])}

    def _is_exempt_path(self, path: str) -> bool:
        normalized = path.rstrip("/") or "/"
        if normalized in self.exempt_paths:
            return True

        return any(
            exempt != "/" and normalized.startswith(f"{exempt}/")
            for exempt in self.exempt_paths
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.bearer_token:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if self._is_exempt_path(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self.bearer_token}"

        if authorization != expected:
            response = JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)