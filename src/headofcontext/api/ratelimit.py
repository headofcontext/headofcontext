"""Per-caller token bucket in front of the routes (ADR 0015).

Keyed by the fingerprint of the bearer token *before* verification, so an abusive client is
turned away without costing an IdP round trip, an engine call or a database write. A bearer seen
for the first time must first take a token from a per-address "new bearer" bucket, so varying
the bearer on every request opens no fresh budget (T18). One bucket set per process: fairness
across replicas belongs to the ingress, and the client address is only meaningful behind a
proxy when uvicorn runs with ``--proxy-headers`` and ``--forwarded-allow-ips``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from headofcontext.core.events import fingerprint

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BUCKETS = 10_000


class TokenBucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float, now: float) -> None:
        self.tokens = capacity
        self.updated = now


class RateLimiter:
    """``per_minute`` requests per key, bursting up to ``per_minute``. ``0`` disables."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._rate = per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def acquire(self, key: str) -> float | None:
        """None when allowed, otherwise seconds to wait for the next token."""
        if self.per_minute <= 0:
            return None
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._open(key, now)
        else:
            self._refill(bucket, now)
        return self._draw(bucket)

    def acquire_request(self, bearer: str | None, address: str) -> float | None:
        """One request from ``address`` carrying ``bearer`` (a fingerprint) or nothing.

        An established bearer spends from its own bucket. A bearer without a bucket first spends
        one token from the address's new-bearer bucket: spraying distinct bearers is throttled
        like any other burst, and a refused attempt opens no bucket at all.
        """
        if self.per_minute <= 0:
            return None
        if bearer is None:
            return self.acquire("addr:" + address)
        key = "bearer:" + bearer
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._refill(bucket, now)
            return self._draw(bucket)
        wait = self.acquire("new:" + address)
        if wait is not None:
            return wait
        return self._draw(self._open(key, now))

    def _open(self, key: str, now: float) -> TokenBucket:
        if len(self._buckets) >= MAX_BUCKETS:
            self._evict(now)
        bucket = self._buckets[key] = TokenBucket(float(self.per_minute), now)
        return bucket

    def _refill(self, bucket: TokenBucket, now: float) -> None:
        bucket.tokens = min(
            float(self.per_minute), bucket.tokens + (now - bucket.updated) * self._rate
        )
        bucket.updated = now

    def _draw(self, bucket: TokenBucket) -> float | None:
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return (1.0 - bucket.tokens) / self._rate

    def _evict(self, now: float) -> None:
        # Drop buckets that refilled completely: they carry no state worth keeping. If that is
        # not enough, drop the half with the most tokens left, oldest first: an exhausted bucket
        # is exactly the state the limiter exists to remember (T18). Never every bucket at once.
        capacity = float(self.per_minute)

        def level(bucket: TokenBucket) -> float:
            return min(capacity, bucket.tokens + (now - bucket.updated) * self._rate)

        for key in [k for k, b in self._buckets.items() if level(b) >= capacity]:
            del self._buckets[key]
        if len(self._buckets) >= MAX_BUCKETS:
            ranked = sorted(self._buckets.items(), key=lambda kv: (-level(kv[1]), kv[1].updated))
            for key, _ in ranked[: len(ranked) // 2]:
                del self._buckets[key]


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, limiter: RateLimiter, *, exempt_paths: frozenset[str] = frozenset()
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._exempt = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self._app(scope, receive, send)
            return
        wait = self._limiter.acquire_request(*_identify(scope))
        if wait is None:
            await self._app(scope, receive, send)
            return
        body = json.dumps({"error": "rate_limited", "detail": "too many requests"}).encode()
        retry_after = str(max(1, int(wait + 0.999)))
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", retry_after.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _identify(scope: Scope) -> tuple[str | None, str]:
    """(bearer fingerprint or None, client address) as seen before any verification."""
    bearer: str | None = None
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            bearer = fingerprint(value)
            break
    client = scope.get("client")
    return bearer, (client[0] if client else "unknown")
