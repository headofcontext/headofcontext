"""Readiness probe (ADR 0020 amendment): one check in flight at a time, blocking work off the
loop, generic answers for anonymous callers, details in the log only."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from headofcontext.core.errors import ConnectorStale
from headofcontext.readiness import ReadinessProbe


class Deps:
    def __init__(self) -> None:
        self.pings = 0
        self.fga = 0

    def ping(self) -> int:
        self.pings += 1
        time.sleep(0.05)  # a slow PostgreSQL connect, synchronous like psycopg
        return 2

    async def engine_ping(self) -> None:
        self.fga += 1

    def assert_fresh(self) -> None:
        raise ConnectorStale("never-synced", float("inf"))


async def test_concurrent_probes_share_one_check_and_do_not_block_the_loop() -> None:
    deps = Deps()
    probe = ReadinessProbe(
        postgres=deps.ping, engine=deps.engine_ping, freshness=deps.assert_fresh, latest=2
    )
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.005)
            ticks += 1

    results, _ = await asyncio.gather(asyncio.gather(*(probe.run() for _ in range(20))), ticker())
    assert deps.pings == 1 and deps.fga == 1
    assert ticks == 5  # the loop kept turning while psycopg slept
    assert all(r == results[0] for r in results)
    ready, checks = results[0]
    assert ready is False
    assert checks == {"postgres": "ok", "schema": "ok", "openfga": "ok", "connectors": "stale"}


async def test_answers_are_generic_and_details_go_to_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> int:
        raise ConnectionError("password authentication failed for user hoc")

    async def engine() -> None:
        raise RuntimeError("store 01ABC not found")

    probe = ReadinessProbe(postgres=broken, engine=engine, freshness=lambda: None, latest=2)
    with caplog.at_level(logging.WARNING):
        ready, checks = await probe.run()
    assert ready is False
    assert checks == {
        "postgres": "error",
        "schema": "unknown",
        "openfga": "error",
        "connectors": "fresh",
    }
    assert "ConnectionError" in caplog.text and "RuntimeError" in caplog.text
    assert "password" not in str(checks)


async def test_schema_behind_is_not_ready() -> None:
    async def engine() -> None:
        return None

    probe = ReadinessProbe(postgres=lambda: 1, engine=engine, freshness=lambda: None, latest=2)
    ready, checks = await probe.run()
    assert ready is False and checks["schema"] == "behind"
