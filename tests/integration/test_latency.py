"""Decision latency without the network hop of the HTTP layer (brief §9: p95 < 20 ms).

Reports the percentiles; asserts only a loose bound so a busy laptop does not fail the suite.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Action, Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from tests.services import FgaStore, load_json

pytestmark = pytest.mark.integration

ROUNDS = 200
LOOSE_P95_MS = 100.0


@pytest.fixture
async def engine(openfga_store: FgaStore) -> AsyncIterator[OpenFgaEngine]:
    engine = OpenFgaEngine.connect(
        openfga_store.url,
        openfga_store.store_id,
        AlwaysFresh(),
        authorization_model_id=openfga_store.model_id,
    )
    yield engine
    await engine.close()


async def test_single_decision_latency(engine: OpenFgaEngine) -> None:
    users = load_json("users.json")
    assert isinstance(users, list)
    subject = users[0]["id"]
    chain = PrincipalChain.root(
        subject, "agent:assistant", Scope.of(Capability(Kind.READ, "document:*"))
    )
    decider = Decider(engine, InMemoryAuditSink())
    await decider.decide(chain, Action.read(), "document:acme-0001")  # warm up
    timings: list[float] = []
    for n in range(ROUNDS):
        started = time.perf_counter()
        await decider.decide(chain, Action.read(), f"document:acme-{(n % 500) + 1:04d}")
        timings.append((time.perf_counter() - started) * 1000)
    ordered = sorted(timings)
    p50, p95 = ordered[len(ordered) // 2], ordered[int(0.95 * (len(ordered) - 1))]
    print(f"\ndecision latency over {ROUNDS} calls: p50={p50:.1f}ms p95={p95:.1f}ms")
    assert p95 < LOOSE_P95_MS
