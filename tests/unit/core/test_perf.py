"""Decision latency off the network: p95 < 20 ms (success criterion) with an in-process engine."""

import statistics
import time
from collections.abc import Sequence

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Action, Capability, Decider, Kind, PrincipalChain, Scope


class InstantEngine:
    async def check(self, subject: str, relation: str, obj: str) -> bool:
        return True

    async def batch_check(self, subject: str, items: Sequence[tuple[str, str]]) -> list[bool]:
        return [True] * len(items)

    async def list_objects(self, subject: str, relation: str, type_: str) -> list[str]:
        return []


async def test_decision_p95_under_20ms() -> None:
    decider = Decider(InstantEngine(), InMemoryAuditSink())
    chain = PrincipalChain.root(
        "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:*"))
    )
    chain = chain.delegate("agent:b", Scope.of(Capability(Kind.READ, "document:hr/*")))
    samples: list[float] = []
    for i in range(500):
        started = time.perf_counter()
        await decider.decide(chain, Action.read(), f"document:hr/{i}")
        samples.append((time.perf_counter() - started) * 1000)
    p95 = statistics.quantiles(samples, n=20)[-1]
    assert p95 < 20, f"p95 = {p95:.2f} ms"
