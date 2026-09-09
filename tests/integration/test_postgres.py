"""Append-only audit journal and revocation store against the real PostgreSQL."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from headofcontext.audit import AuditEvent, EventKind, InMemoryAuditSink, PostgresAuditSink
from headofcontext.core import Capability, Kind, PrincipalChain, Scope
from headofcontext.core.errors import TokenRevoked
from headofcontext.tokens.biscuit import KeyRing, PostgresRevocationStore, TokenService

pytestmark = pytest.mark.integration


def _event(i: int) -> AuditEvent:
    return AuditEvent(
        kind=EventKind.DECISION,
        timestamp=datetime.now(UTC),
        subject="user:alice",
        actor="agent:a",
        delegation_depth=0,
        action="read:viewer",
        resource=f"document:{i}",
        outcome="ALLOW",
        reason="allowed",
        decision_id=f"d{i}",
    )


@pytest.fixture
def sink(pg_dsn: str) -> PostgresAuditSink:
    sink = PostgresAuditSink(pg_dsn)
    sink.ensure_schema()
    return sink


def test_hash_chain_survives_roundtrip(sink: PostgresAuditSink) -> None:
    before = sink.count()
    for i in range(5):
        sink.record(_event(i))
    assert sink.count() == before + 5
    assert sink.verify_chain()


def test_t10_update_and_delete_rejected(sink: PostgresAuditSink, pg_dsn: str) -> None:
    sink.record(_event(99))
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.Error, match="append-only"):
            conn.execute("UPDATE audit_events SET outcome = 'DENY' WHERE decision_id = 'd99'")
        with pytest.raises(psycopg.Error, match="append-only"):
            conn.execute("DELETE FROM audit_events WHERE decision_id = 'd99'")
    assert sink.verify_chain()


def test_revocation_store_roundtrip(pg_dsn: str) -> None:
    store = PostgresRevocationStore(pg_dsn)
    store.ensure_schema()
    service = TokenService(
        KeyRing.generate(1), store, InMemoryAuditSink(), ttl=timedelta(minutes=5)
    )
    chain = PrincipalChain.root(
        "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:*"))
    )
    issued = service.issue(chain)
    child = service.attenuate(issued.token, to_actor="agent:b", scope=chain.scope)
    service.verify(child.token, caller="agent:b", operation=Kind.READ, resource="document:x")
    service.revoke_id(issued.revocation_ids[0], reason="integration test")
    with pytest.raises(TokenRevoked):
        service.verify(child.token, caller="agent:b", operation=Kind.READ, resource="document:x")
    store.close()


def test_verify_chain_report_streams_the_journal(pg_dsn: str) -> None:
    from headofcontext.audit import PostgresAuditSink

    report = PostgresAuditSink(pg_dsn).verify_chain_report(batch=50)
    assert report.ok and report.checked >= 1 and report.first_seq is not None
    exported = list(PostgresAuditSink(pg_dsn).export(batch=50))
    assert len(exported) == report.checked and exported[0]["seq"] == report.first_seq


def test_approval_store_roundtrip(pg_dsn: str) -> None:
    from datetime import timedelta

    from headofcontext.actions import ApprovalRequest, ApprovalStatus, PostgresApprovalStore

    store = PostgresApprovalStore(pg_dsn)
    store.ensure_schema()
    now = datetime.now(UTC)
    request = ApprovalRequest(
        request_id=f"it-{now.timestamp()}",
        subject="user:alice",
        actor="agent:a",
        delegation_depth=1,
        tool="tool:payment.send",
        args_hash="abc",
        decision_id="d1",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    store.create(request)
    assert store.get(request.request_id) == request
    assert request in store.list_pending(subject="user:alice")
    approved = request.with_status(
        ApprovalStatus.APPROVED, resolved_by="user:carol", resolved_at=now, resolution_reason="ok"
    )
    store.update(approved)
    fetched = store.get(request.request_id)
    assert (
        fetched is not None
        and fetched.status is ApprovalStatus.APPROVED
        and fetched.resolved_by == "user:carol"
    )
    assert request not in store.list_pending()
    # Resolution is a compare-and-set from PENDING: a second resolver gets None.
    assert (
        store.transition(
            request.request_id, (ApprovalStatus.PENDING,), ApprovalStatus.REJECTED, resolved_by="x"
        )
        is None
    )
    # ADR 0017: consumption is a compare-and-set, one winner only.
    consumed = store.consume(request.request_id, now)
    assert consumed is not None and consumed.status is ApprovalStatus.CONSUMED
    assert store.consume(request.request_id, now) is None
    assert store.consume("never-created", now) is None
    store.close()


def test_connector_state_is_scoped_by_namespace(pg_dsn: str) -> None:
    from headofcontext.read.connectors import PostgresConnectorStateStore

    a = PostgresConnectorStateStore(pg_dsn, timedelta(hours=1), namespace="store-a")
    b = PostgresConnectorStateStore(pg_dsn, timedelta(hours=1), namespace="store-b")
    a.ensure_schema()
    now = datetime.now(UTC)
    a.save("nextcloud", frozenset({("user:x", "viewer", "document:y")}), now)
    assert a.load_tuples("nextcloud") == frozenset({("user:x", "viewer", "document:y")})
    assert b.load_tuples("nextcloud") is None
    b.register("nextcloud")
    a.assert_fresh()
    with pytest.raises(Exception, match="nextcloud"):
        b.assert_fresh()
    a.close()
    b.close()


def test_mandate_store_roundtrip(pg_dsn: str) -> None:
    from datetime import UTC, datetime, timedelta

    from headofcontext.core import Capability, Kind, Scope
    from headofcontext.tokens.mandates import Mandate, MandateStatus, PostgresMandateStore

    store = PostgresMandateStore(pg_dsn)
    store.ensure_schema()
    now = datetime.now(UTC).replace(microsecond=0)
    subject = f"user:pg-{now.timestamp():.0f}"
    live = Mandate(
        mandate_id=f"m-{now.timestamp():.0f}-a",
        subject=subject,
        agent="agent:assistant",
        scope=Scope.of(Capability(Kind.ACT, "tool:mail.*"), Capability(Kind.READ, "document:*")),
        created_at=now,
        expires_at=now + timedelta(days=1),
        max_token_ttl=timedelta(minutes=30),
        created_by=subject,
    )
    stale = Mandate(
        mandate_id=f"m-{now.timestamp():.0f}-b",
        subject=subject,
        agent="agent:mailer",
        scope=Scope.of(Capability(Kind.ACT, "tool:mail.send")),
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
        max_token_ttl=timedelta(minutes=5),
        created_by=subject,
    )
    store.create(live)
    store.create(stale)
    try:
        assert store.get(live.mandate_id) == live
        assert [m.mandate_id for m in store.list_for(subject)] == [
            stale.mandate_id,
            live.mandate_id,
        ]
        expired = store.expire_active(now)
        assert [m.mandate_id for m in expired] == [stale.mandate_id]
        assert store.get(stale.mandate_id).status is MandateStatus.EXPIRED  # type: ignore[union-attr]
        store.update(live.with_status(MandateStatus.REVOKED, revoked_at=now, revocation_reason="x"))
        got = store.get(live.mandate_id)
        assert (
            got is not None and got.status is MandateStatus.REVOKED and got.revocation_reason == "x"
        )
    finally:
        store.close()
