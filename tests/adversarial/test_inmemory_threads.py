"""T16 / T10 for the in-memory stores now that services run them in threads (ADR 0022)."""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from headofcontext.actions import ApprovalRequest, ApprovalStatus, InMemoryApprovalStore
from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.events import AuditEvent, EventKind
from headofcontext.tokens.mandates import InMemoryMandateStore, Mandate, MandateStatus

pytestmark = pytest.mark.adversarial
THREADS = 8
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def tiny_switch_interval() -> None:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield  # type: ignore[misc]
    sys.setswitchinterval(previous)


def _race(fn: object, workers: int = THREADS) -> None:
    barrier = threading.Barrier(workers)

    def run() -> None:
        barrier.wait()
        fn()  # type: ignore[operator]

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_t16_inmemory_consume_has_exactly_one_winner_under_threads() -> None:
    store = InMemoryApprovalStore()
    store.create(
        ApprovalRequest(
            request_id="r",
            subject="user:a",
            actor="agent:a",
            delegation_depth=0,
            tool="tool:pay",
            args_hash="h",
            decision_id="d",
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            status=ApprovalStatus.APPROVED,
        )
    )
    winners: list[ApprovalRequest] = []
    lock = threading.Lock()

    def attempt() -> None:
        for _ in range(50):
            got = store.consume("r", NOW)
            if got is not None:
                with lock:
                    winners.append(got)

    for _ in range(20):
        winners.clear()
        store.update(store.get("r").with_status(ApprovalStatus.APPROVED))  # type: ignore[union-attr]
        _race(attempt)
        assert len(winners) == 1


def test_t10_inmemory_audit_chain_survives_concurrent_writers() -> None:
    sink = InMemoryAuditSink()

    def write() -> None:
        for i in range(200):
            sink.record(
                AuditEvent(
                    kind=EventKind.DECISION,
                    timestamp=NOW,
                    subject="user:a",
                    actor="agent:a",
                    delegation_depth=0,
                    action="act:can_invoke",
                    resource=f"tool:{i}",
                    outcome="ALLOW",
                    reason="allowed",
                )
            )

    _race(write)
    assert len(sink.events) == THREADS * 200
    assert sink.verify_chain()


def test_inmemory_mandate_sweep_is_atomic_under_threads() -> None:
    store = InMemoryMandateStore()
    for i in range(50):
        store.create(
            Mandate(
                mandate_id=f"m{i}",
                subject="user:a",
                agent="agent:a",
                scope=Scope.of(Capability(Kind.ACT, "tool:*")),
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=1),
                max_token_ttl=timedelta(minutes=5),
                created_by="user:a",
            )
        )
    expired: list[Mandate] = []
    lock = threading.Lock()

    def sweep() -> None:
        got = store.expire_active(NOW + timedelta(minutes=2))
        with lock:
            expired.extend(got)

    _race(sweep)
    assert len(expired) == 50  # every mandate expired exactly once
    assert all(m.status is MandateStatus.EXPIRED for m in store.list_for("user:a"))
