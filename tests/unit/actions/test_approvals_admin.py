"""Sweeping expired requests and operator-side resolution shared with the gate (ADR 0012)."""

from datetime import UTC, datetime, timedelta

import pytest

from headofcontext.actions import (
    ApprovalRequest,
    ApprovalStatus,
    InMemoryApprovalStore,
    resolve_request,
)
from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core.errors import ApprovalError

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _request(request_id: str, minutes: int) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        subject="user:alice",
        actor="agent:a",
        delegation_depth=0,
        tool="tool:payment.send",
        args_hash="h",
        decision_id="d",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=minutes),
    )


def test_expire_pending_marks_only_overdue() -> None:
    store = InMemoryApprovalStore()
    store.create(_request("old", 5))
    store.create(_request("fresh", 60))
    store.create(_request("done", 5).with_status(ApprovalStatus.APPROVED))
    assert store.expire_pending(NOW + timedelta(minutes=10)) == 1
    assert store.get("old").status is ApprovalStatus.EXPIRED  # type: ignore[union-attr]
    assert store.get("fresh").status is ApprovalStatus.PENDING  # type: ignore[union-attr]
    assert store.get("done").status is ApprovalStatus.APPROVED  # type: ignore[union-attr]


def test_resolve_request_is_audited_and_guards_self_approval() -> None:
    store, audit = InMemoryApprovalStore(), InMemoryAuditSink()
    store.create(_request("r1", 60))
    with pytest.raises(ApprovalError):
        resolve_request(store, audit, NOW, "r1", approver="user:alice", approved=True)
    resolved = resolve_request(
        store, audit, NOW, "r1", approver="user:carol", approved=False, reason="no"
    )
    assert resolved.status is ApprovalStatus.REJECTED and resolved.resolved_by == "user:carol"
    assert (
        audit.events[-1].kind is EventKind.APPROVAL_RESOLVED
        and audit.events[-1].actor == "user:carol"
    )


def test_consume_is_a_compare_and_set() -> None:
    store = InMemoryApprovalStore()
    store.create(_request("pending", 60))
    store.create(_request("ok", 60).with_status(ApprovalStatus.APPROVED))
    assert store.consume("pending", NOW) is None
    assert store.consume("missing", NOW) is None
    consumed = store.consume("ok", NOW)
    assert (
        consumed is not None
        and consumed.status is ApprovalStatus.CONSUMED
        and consumed.consumed_at == NOW
    )
    assert store.consume("ok", NOW) is None
    assert store.get("ok") == consumed


def test_transition_is_a_compare_and_set_from_pending() -> None:
    store = InMemoryApprovalStore()
    store.create(_request("r1", 60))
    rejected = store.transition(
        "r1",
        (ApprovalStatus.PENDING,),
        ApprovalStatus.REJECTED,
        resolved_by="user:carol",
        resolved_at=NOW,
        resolution_reason="no",
    )
    assert rejected is not None and rejected.status is ApprovalStatus.REJECTED
    late = store.transition(
        "r1", (ApprovalStatus.PENDING,), ApprovalStatus.APPROVED, resolved_by="user:dave"
    )
    assert late is None
    assert store.get("r1").status is ApprovalStatus.REJECTED  # type: ignore[union-attr]
    assert store.transition("missing", (ApprovalStatus.PENDING,), ApprovalStatus.EXPIRED) is None
