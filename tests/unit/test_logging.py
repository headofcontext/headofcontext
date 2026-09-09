"""Logging shape (ADR 0025): JSON lines with a request id, level from settings."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from headofcontext.api.requestid import RequestIdMiddleware
from headofcontext.logging import JsonFormatter, RequestIdFilter, configure_logging, request_id_var


def test_json_formatter_carries_the_request_id() -> None:
    record = logging.LogRecord("hoc.test", logging.WARNING, __file__, 1, "hello %s", ("x",), None)
    token = request_id_var.set("req-42")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello x" and line["level"] == "WARNING"
    assert line["logger"] == "hoc.test" and line["request_id"] == "req-42"


def test_configure_logging_sets_level_and_format() -> None:
    configure_logging("debug", "json")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    configure_logging("INFO", "text")
    assert root.level == logging.INFO


def test_request_id_is_echoed_or_generated() -> None:
    app = FastAPI()
    seen: list[str | None] = []

    @app.get("/x")
    async def x() -> dict[str, str]:
        seen.append(request_id_var.get())
        return {"ok": "1"}

    app.add_middleware(RequestIdMiddleware)
    client = TestClient(app)
    echoed = client.get("/x", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123" and seen[-1] == "abc-123"
    generated = client.get("/x")
    assert (
        len(generated.headers["x-request-id"]) == 32
        and seen[-1] == generated.headers["x-request-id"]
    )
    hostile = client.get("/x", headers={"X-Request-ID": "x" * 500})
    assert hostile.headers["x-request-id"] != "x" * 500


def test_request_id_is_ascii_only() -> None:
    app = FastAPI()

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {"ok": "1"}

    app.add_middleware(RequestIdMiddleware)
    client = TestClient(app)
    odd = client.get("/x", headers={b"x-request-id": "caf\u00e9-id".encode("latin-1")})
    assert len(odd.headers["x-request-id"]) == 32  # replaced by a generated id


def test_rate_limited_answers_still_carry_a_request_id() -> None:
    from headofcontext.api.ratelimit import RateLimiter, RateLimitMiddleware

    app = FastAPI()

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {"ok": "1"}

    # Same order as create_app: the limiter first, the request id outermost.
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(1), exempt_paths=frozenset())
    app.add_middleware(RequestIdMiddleware)
    client = TestClient(app)
    assert client.get("/x").status_code == 200
    limited = client.get("/x", headers={"X-Request-ID": "trace-9"})
    assert limited.status_code == 429 and limited.headers["x-request-id"] == "trace-9"
