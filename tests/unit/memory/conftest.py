"""A tiny relationship graph standing in for OpenFGA in memory unit tests.

It evaluates only what the memory layer needs: `viewer` on documents from direct tuples, and the
tuple store operations. Integration tests use the real container.
"""

from __future__ import annotations

import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.memory import InMemoryLedger, InMemoryMemoryAdapter, MemoryService
from tests.conftest import FrozenClock
from tests.support.fakes import FakeGraph

HR_DOC = "document:hr/salaries"
LILLE_DOC = "document:lille/planning"
FULL_SCOPE = Scope.of(
    Capability(Kind.READ, "document:*"),
    Capability(Kind.READ, "memory:*"),
    Capability(Kind.REMEMBER, "document:*"),
    Capability(Kind.REMEMBER, "memory:*"),
)


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    g.grant("user:alice", "viewer", HR_DOC)  # alice is HR
    g.grant("user:alice", "viewer", LILLE_DOC)
    g.grant("user:bob", "viewer", LILLE_DOC)  # bob is a store employee
    return g


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def ledger() -> InMemoryLedger:
    return InMemoryLedger()


@pytest.fixture
def adapter() -> InMemoryMemoryAdapter:
    return InMemoryMemoryAdapter()


@pytest.fixture
def service(
    graph: FakeGraph,
    audit: InMemoryAuditSink,
    ledger: InMemoryLedger,
    adapter: InMemoryMemoryAdapter,
    clock: FrozenClock,
) -> MemoryService:
    return MemoryService(
        engine=graph,
        tuples=graph,
        ledger=ledger,
        adapter=adapter,
        decider=Decider(graph, audit, clock),
        audit=audit,
        clock=clock,
    )


def chain_for(
    subject: str, actor: str = "agent:assistant", scope: Scope = FULL_SCOPE
) -> PrincipalChain:
    return PrincipalChain.root(subject, actor, scope)
