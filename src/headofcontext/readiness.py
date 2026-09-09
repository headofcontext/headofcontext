"""Readiness probe (ADR 0020, amended): one check in flight, blocking work off the loop.

The probe is anonymous and cheap to call, so it must never be a way to stall the worker: the
synchronous PostgreSQL connect runs in a thread, concurrent probes share the check that is
already running (a coalescer, not a cache), and the public answer names states, not causes.
Causes go to the log.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from headofcontext.core.errors import ConnectorStale

log = logging.getLogger(__name__)

Checks = dict[str, str]
READY_STATES = frozenset({"ok", "fresh"})


class ReadinessProbe:
    def __init__(
        self,
        *,
        postgres: Callable[[], int],
        engine: Callable[[], Awaitable[None]],
        freshness: Callable[[], None],
        latest: int,
    ) -> None:
        self._postgres = postgres
        self._engine = engine
        self._freshness = freshness
        self._latest = latest
        self._inflight: asyncio.Task[tuple[bool, Checks]] | None = None

    async def run(self) -> tuple[bool, Checks]:
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._check())
        return await asyncio.shield(self._inflight)

    async def _check(self) -> tuple[bool, Checks]:
        checks: Checks = {}
        try:
            version = await asyncio.to_thread(self._postgres)
            checks["postgres"] = "ok"
            if version == self._latest:
                checks["schema"] = "ok"
            else:
                checks["schema"] = "behind" if version < self._latest else "ahead"
                log.warning("readiness: schema at %d, code expects %d", version, self._latest)
        except Exception as exc:
            checks["postgres"] = "error"
            checks["schema"] = "unknown"
            log.warning("readiness: postgres check failed: %s", type(exc).__name__)
        try:
            await self._engine()
            checks["openfga"] = "ok"
        except Exception as exc:
            checks["openfga"] = "error"
            log.warning("readiness: openfga check failed: %s", type(exc).__name__)
        try:
            self._freshness()
            checks["connectors"] = "fresh"
        except ConnectorStale as exc:
            checks["connectors"] = "stale"
            log.warning("readiness: connector %s is stale", exc.connector)
        except Exception as exc:
            checks["connectors"] = "error"
            log.warning("readiness: freshness check failed: %s", type(exc).__name__)
        return all(v in READY_STATES for v in checks.values()), checks


__all__ = ["ReadinessProbe"]
