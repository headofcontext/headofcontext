"""X-Request-ID in, X-Request-ID out, bound to the logging context (ADR 0025)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from headofcontext.logging import request_id_var

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

HEADER = b"x-request-id"
MAX_LENGTH = 128


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = _incoming(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER, request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def _incoming(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == HEADER:
            candidate = str(bytes(value).decode("latin-1")).strip()
            if 0 < len(candidate) <= MAX_LENGTH and candidate.isascii() and candidate.isprintable():
                return candidate
    return None


__all__ = ["RequestIdMiddleware"]
