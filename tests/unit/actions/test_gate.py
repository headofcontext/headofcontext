"""ActionGate: gate / resolve / redeem with persistent approval state (ADR 0008)."""

from datetime import timedelta

import pytest

from headofcontext.actions import (
    ActionGate,
    ApprovalError,
    ApprovalStatus,
    InMemoryApprovalStore,
    RequireApproval,
)
from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, Outcome, PrincipalChain, Scope
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

ACT_ALL = Scope.of(Capability(Kind.ACT, "tool:*"))
ALICE = PrincipalChain.root("user:alice", "agent:a", ACT_ALL)
PAY = {"amount": 500, "to": "IBAN-1"}


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    g.grant("user:alice", "can_invoke", "tool:mail.send")
    g.grant("user:alice", "can_invoke", "tool:payment.send")
    g.grant("user:bob", "can_invoke", "tool:mail.send")
    g.grant("user:carol", "approver", "tool:payment.send")
    return g


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def approvals() -> InMemoryApprovalStore:
    return InMemoryApprovalStore()


@pytest.fixture
def gate(
    graph: FakeGraph, audit: InMemoryAuditSink, approvals: InMemoryApprovalStore, clock: FrozenClock
) -> ActionGate:
    decider = Decider(graph, audit, clock, policy=RequireApproval(["tool:payment.*"]))
    return ActionGate(decider, approvals, audit, clock, approval_ttl=timedelta(minutes=30))


class TestGate:
    async def test_allow(self, gate: ActionGate) -> None:
        result = await gate.gate(ALICE, "tool:mail.send", {"to": "bob@acme.example"})
        assert result.allowed and result.approval is None
        assert result.decision.outcome is Outcome.ALLOW

    async def test_deny_without_tuple(self, gate: ActionGate) -> None:
        bob = PrincipalChain.root("user:bob", "agent:a", ACT_ALL)
        result = await gate.gate(bob, "tool:payment.send", PAY)
        assert not result.allowed and result.approval is None
        assert result.decision.reason == "not_related"

    async def test_deny_outside_scope(self, gate: ActionGate) -> None:
        narrow = PrincipalChain.root(
            "user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:mail.*"))
        )
        result = await gate.gate(narrow, "tool:payment.send", PAY)
        assert result.decision.reason == "scope_excluded"

    async def test_require_approval_creates_pending_request(
        self,
        gate: ActionGate,
        approvals: InMemoryApprovalStore,
        audit: InMemoryAuditSink,
        clock: FrozenClock,
    ) -> None:
        result = await gate.gate(ALICE, "tool:payment.send", PAY)
        assert result.pending and not result.allowed
        request = result.approval
        assert request is not None
        assert request.status is ApprovalStatus.PENDING
        assert (
            request.subject == "user:alice"
            and request.actor == "agent:a"
            and request.tool == "tool:payment.send"
        )
        assert request.expires_at == clock.now() + timedelta(minutes=30)
        assert approvals.get(request.request_id) == request
        assert "IBAN" not in repr(request)
        assert EventKind.APPROVAL_REQUESTED in [e.kind for e in audit.events]


class TestResolve:
    async def test_approve_then_redeem(self, gate: ActionGate, audit: InMemoryAuditSink) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        resolved = await gate.resolve(
            request.request_id, approver="user:carol", approved=True, reason="ok"
        )
        assert resolved.status is ApprovalStatus.APPROVED and resolved.resolved_by == "user:carol"
        decision = await gate.redeem(ALICE, request.request_id, PAY)
        assert decision.outcome is Outcome.ALLOW
        assert gate.approvals.get(request.request_id).status is ApprovalStatus.CONSUMED  # type: ignore[union-attr]
        kinds = [e.kind for e in audit.events]
        assert EventKind.APPROVAL_RESOLVED in kinds and EventKind.APPROVAL_CONSUMED in kinds

    async def test_reject(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        await gate.resolve(request.request_id, approver="user:carol", approved=False, reason="no")
        decision = await gate.redeem(ALICE, request.request_id, PAY)
        assert decision.outcome is Outcome.DENY and decision.reason == "approval_not_approved"

    async def test_self_approval_refused(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        with pytest.raises(ApprovalError):
            await gate.resolve(request.request_id, approver="user:alice", approved=True)

    async def test_approver_must_be_user(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        with pytest.raises(ApprovalError):
            await gate.resolve(request.request_id, approver="agent:approver-bot", approved=True)

    async def test_resolve_twice_refused(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        await gate.resolve(request.request_id, approver="user:carol", approved=True)
        with pytest.raises(ApprovalError):
            await gate.resolve(request.request_id, approver="user:dave", approved=False)

    async def test_resolve_expired_refused(self, gate: ActionGate, clock: FrozenClock) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        clock.tick(minutes=31)
        with pytest.raises(ApprovalError):
            await gate.resolve(request.request_id, approver="user:carol", approved=True)
        assert gate.approvals.get(request.request_id).status is ApprovalStatus.EXPIRED  # type: ignore[union-attr]

    async def test_unknown_request(self, gate: ActionGate) -> None:
        with pytest.raises(ApprovalError):
            await gate.resolve("nope", approver="user:carol", approved=True)

    async def test_self_approval_needs_no_approver_tuple_when_enabled(
        self, graph: FakeGraph, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        decider = Decider(graph, audit, clock, policy=RequireApproval(["tool:payment.*"]))
        solo = ActionGate(decider, InMemoryApprovalStore(), audit, clock, allow_self_approval=True)
        request = (await solo.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        resolved = await solo.resolve(request.request_id, approver="user:alice", approved=True)
        assert resolved.status is ApprovalStatus.APPROVED
        # Self-approval does not open the door to other people's requests.
        bob = PrincipalChain.root("user:bob", "agent:a", ACT_ALL)
        graph.grant("user:bob", "can_invoke", "tool:payment.send")
        other = (await solo.gate(bob, "tool:payment.send", PAY)).approval
        assert other is not None
        with pytest.raises(ApprovalError):
            await solo.resolve(other.request_id, approver="user:alice", approved=True)

    async def test_pending_for_lists_approvable_and_own(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        assert await gate.pending_for("user:carol") == [request]
        assert await gate.pending_for("user:alice") == [request]
        assert await gate.pending_for("user:bob") == []


class TestRedeem:
    async def test_pending_cannot_be_redeemed(self, gate: ActionGate) -> None:
        request = (await gate.gate(ALICE, "tool:payment.send", PAY)).approval
        assert request is not None
        decision = await gate.redeem(ALICE, request.request_id, PAY)
        assert decision.outcome is Outcome.DENY and decision.reason == "approval_not_approved"

    async def test_unknown_request_denies(self, gate: ActionGate) -> None:
        decision = await gate.redeem(ALICE, "nope", PAY)
        assert decision.outcome is Outcome.DENY and decision.reason == "approval_unknown"

    async def test_list_pending(self, gate: ActionGate, approvals: InMemoryApprovalStore) -> None:
        await gate.gate(ALICE, "tool:payment.send", PAY)
        await gate.gate(ALICE, "tool:payment.send", {"amount": 1})
        assert len(approvals.list_pending()) == 2
        assert len(approvals.list_pending(subject="user:bob")) == 0
