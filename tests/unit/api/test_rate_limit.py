"""Rate limiting (ADR 0015): per caller, before any verification, health exempt."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from headofcontext.api.ratelimit import RateLimiter, RateLimitMiddleware


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_app(per_minute: int, clock: Clock) -> tuple[TestClient, list[str]]:
    hits: list[str] = []
    app = FastAPI()

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/v1/actions/gate")
    async def gate() -> dict[str, Any]:
        hits.append("gate")
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(per_minute, clock),
        exempt_paths=frozenset({"/v1/health"}),
    )
    return TestClient(app), hits


def test_bucket_refills_over_time() -> None:
    clock = Clock()
    limiter = RateLimiter(60, clock)  # one per second
    assert all(limiter.acquire("k") is None for _ in range(60))
    wait = limiter.acquire("k")
    assert wait is not None and 0 < wait <= 1.0
    clock.t += 2
    assert limiter.acquire("k") is None
    assert limiter.acquire("k") is None
    assert limiter.acquire("k") is not None


def test_zero_disables() -> None:
    limiter = RateLimiter(0)
    assert all(limiter.acquire("k") is None for _ in range(1000))


def test_over_limit_is_429_and_never_reaches_the_route() -> None:
    clock = Clock()
    client, hits = make_app(2, clock)
    headers = {"Authorization": "Bearer agent-one"}
    assert client.post("/v1/actions/gate", headers=headers).status_code == 200
    assert client.post("/v1/actions/gate", headers=headers).status_code == 200
    third = client.post("/v1/actions/gate", headers=headers)
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1
    assert third.json()["error"] == "rate_limited"
    assert hits == ["gate", "gate"]


def test_buckets_are_per_bearer_then_per_address() -> None:
    clock = Clock()
    client, hits = make_app(2, clock)
    assert client.post("/v1/actions/gate", headers={"Authorization": "Bearer a"}).status_code == 200
    assert client.post("/v1/actions/gate", headers={"Authorization": "Bearer b"}).status_code == 200
    assert client.post("/v1/actions/gate", headers={"Authorization": "Bearer a"}).status_code == 200
    assert client.post("/v1/actions/gate", headers={"Authorization": "Bearer a"}).status_code == 429
    assert client.post("/v1/actions/gate").status_code == 200
    assert client.post("/v1/actions/gate").status_code == 200
    assert client.post("/v1/actions/gate").status_code == 429
    assert len(hits) == 5


def test_spraying_new_bearers_from_one_address_is_throttled() -> None:
    """T18: a fresh garbage bearer per request must not open a fresh bucket per request."""
    clock = Clock()
    client, hits = make_app(3, clock)
    for i in range(3):
        headers = {"Authorization": f"Bearer garbage-{i}"}
        assert client.post("/v1/actions/gate", headers=headers).status_code == 200
    sprayed = client.post("/v1/actions/gate", headers={"Authorization": "Bearer garbage-3"})
    assert sprayed.status_code == 429
    # Header casing or spacing does not make a "new" bearer free either.
    assert (
        client.post("/v1/actions/gate", headers={"Authorization": "bearer garbage-0"}).status_code
        == 429
    )
    # An established bearer keeps its own budget while the spray is refused.
    assert (
        client.post("/v1/actions/gate", headers={"Authorization": "Bearer garbage-0"}).status_code
        == 200
    )
    assert len(hits) == 4
    clock.t += 60
    assert (
        client.post("/v1/actions/gate", headers={"Authorization": "Bearer new"}).status_code == 200
    )


def test_health_is_exempt() -> None:
    clock = Clock()
    client, _ = make_app(1, clock)
    assert all(client.get("/v1/health").status_code == 200 for _ in range(5))


def test_eviction_keeps_memory_bounded() -> None:
    from headofcontext.api import ratelimit

    clock = Clock()
    limiter = RateLimiter(60, clock)
    for i in range(ratelimit.MAX_BUCKETS):
        limiter.acquire(f"k{i}")
    clock.t += 120
    limiter.acquire("fresh")
    assert len(limiter._buckets) <= 2


def test_eviction_under_pressure_keeps_the_most_recent_buckets() -> None:
    """T18: filling the table must not reset every legitimate caller's budget."""
    from headofcontext.api import ratelimit

    clock = Clock()
    limiter = RateLimiter(60, clock)
    for i in range(ratelimit.MAX_BUCKETS):
        clock.t += 0.001
        limiter.acquire(f"k{i}")
    hot = f"k{ratelimit.MAX_BUCKETS - 1}"
    before = limiter._buckets[hot].tokens
    limiter.acquire("fresh")
    assert len(limiter._buckets) <= ratelimit.MAX_BUCKETS // 2 + 1
    assert "k0" not in limiter._buckets
    assert limiter._buckets[hot].tokens == before


def test_exhausted_bearer_stays_exhausted_when_the_table_is_under_pressure() -> None:
    """At the default 600/min a bucket refills in 60 s, not 6 s: eviction must not reopen it."""
    from headofcontext.api import ratelimit

    clock = Clock()
    limiter = RateLimiter(600, clock)
    for _ in range(600):
        assert limiter.acquire("bearer:hot") is None
    assert limiter.acquire("bearer:hot") is not None
    for i in range(ratelimit.MAX_BUCKETS):
        limiter.acquire(f"k{i}")
    clock.t += 7
    limiter.acquire("fresh")  # triggers eviction
    # 7 s at 10 tokens/s refills 70 requests, not a fresh 600.
    allowed = sum(1 for _ in range(600) if limiter.acquire("bearer:hot") is None)
    assert allowed <= 70
