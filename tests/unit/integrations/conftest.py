"""Token + gate composition fixtures: a real TokenService over an in-memory graph."""

from __future__ import annotations

from datetime import timedelta

import pytest

from headofcontext.actions import ActionGate, InMemoryApprovalStore, RequireApproval
from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.integrations import AgentSession
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

ACT_ALL = Scope.of(Capability(Kind.ACT, "tool:*"))
MAIL_ONLY = Scope.of(Capability(Kind.ACT, "tool:mail.*"))
ALICE = PrincipalChain.root("user:alice", "agent:assistant", ACT_ALL)


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    g.grant("user:alice", "can_invoke", "tool:mail.send")
    g.grant("user:alice", "can_invoke", "tool:hr.export")
    g.grant("user:alice", "can_invoke", "tool:payment.send")
    g.grant("user:carol", "approver", "tool:payment.send")
    g.grant("user:bob", "approver", "tool:payment.send")
    return g


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def token_service(audit: InMemoryAuditSink, clock: FrozenClock) -> TokenService:
    return TokenService(
        KeyRing.generate(1), InMemoryRevocationStore(), audit, clock, ttl=timedelta(hours=1)
    )


@pytest.fixture
def gate(graph: FakeGraph, audit: InMemoryAuditSink, clock: FrozenClock) -> ActionGate:
    decider = Decider(graph, audit, clock, policy=RequireApproval(["tool:payment.*"]))
    return ActionGate(decider, InMemoryApprovalStore(), audit, clock)


@pytest.fixture
def session(token_service: TokenService, gate: ActionGate) -> AgentSession:
    token = token_service.issue(ALICE).token
    return AgentSession(
        token=token, caller="agent:assistant", token_service=token_service, gate=gate
    )
