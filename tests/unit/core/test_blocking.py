"""ADR 0022: the event loop keeps turning while a store answers (decider, gate, memory)."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from headofcontext.actions import ActionGate, InMemoryApprovalStore, RequireApproval
from headofcontext.audit import InMemoryAuditSink
from headofcontext.audit.events import AuditEvent
from headofcontext.core import Action, Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.memory import InMemoryLedger, InMemoryMemoryAdapter, MemoryService
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FULL_SCOPE, HR_DOC, FakeGraph

ALICE = PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*")))
SLEEP = 0.05


class SlowSink(InMemoryAuditSink):
    def record(self, event: AuditEvent) -> None:
        time.sleep(SLEEP)  # a PostgreSQL round trip, synchronous like psycopg
        super().record(event)


class SlowApprovals(InMemoryApprovalStore):
    def create(self, request):  # type: ignore[no-untyped-def]
        time.sleep(SLEEP)
        super().create(request)


class SlowLedger(InMemoryLedger):
    def get_by_backend_ref(self, backend: str, backend_ref: str):  # type: ignore[no-untyped-def]
        time.sleep(SLEEP)
        return super().get_by_backend_ref(backend, backend_ref)


async def _ticks_during(coro):  # type: ignore[no-untyped-def]
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(SLEEP / 10)
            ticks += 1

    result, _ = await asyncio.gather(coro, ticker())
    return result, ticks


async def test_decider_audit_write_does_not_block_the_loop(clock: FrozenClock) -> None:
    graph = FakeGraph()
    graph.grant("user:alice", "can_invoke", "tool:mail.send")
    decider = Decider(graph, SlowSink(), clock)
    decision, ticks = await _ticks_during(decider.decide(ALICE, Action.invoke(), "tool:mail.send"))
    assert decision.outcome.value == "ALLOW" and ticks == 20


async def test_gate_store_write_does_not_block_the_loop(clock: FrozenClock) -> None:
    graph = FakeGraph()
    graph.grant("user:alice", "can_invoke", "tool:payment.send")
    audit = SlowSink()
    decider = Decider(graph, audit, clock, policy=RequireApproval(["tool:payment.*"]))
    gate = ActionGate(decider, SlowApprovals(), audit, clock, approval_ttl=timedelta(minutes=5))
    result, ticks = await _ticks_during(gate.gate(ALICE, "tool:payment.send", {"amount": 1}))
    assert result.pending and ticks == 20


async def test_memory_ledger_read_does_not_block_the_loop(clock: FrozenClock) -> None:
    graph = FakeGraph()
    graph.grant("user:alice", "viewer", HR_DOC)
    audit = SlowSink()
    ledger = SlowLedger()
    service = MemoryService(
        engine=graph,
        tuples=graph,
        ledger=ledger,
        adapter=InMemoryMemoryAdapter(),
        decider=Decider(graph, audit, clock),
        audit=audit,
        clock=clock,
    )
    chain = PrincipalChain.root("user:alice", "agent:assistant", FULL_SCOPE)
    await service.remember(chain, "grille salaires 2026", derived_from=[HR_DOC])
    found, ticks = await _ticks_during(service.recall(chain, "salaires"))
    assert len(found) == 1 and ticks == 20
