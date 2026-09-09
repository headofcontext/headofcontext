"""Every store maps a database failure to its typed `Unavailable` error (I5, ADR 0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from headofcontext.actions.approvals import ApprovalStoreUnavailable, PostgresApprovalStore
from headofcontext.audit.postgres import PostgresAuditSink
from headofcontext.core.errors import AuditUnavailable, ConnectorStale, Unavailable
from headofcontext.db import pool
from headofcontext.memory.ledger import LedgerUnavailable, PostgresLedger
from headofcontext.memory.postgres import MemoryContentUnavailable, PostgresMemoryAdapter
from headofcontext.read.connectors.base import (
    ConnectorStateUnavailable,
    PostgresConnectorStateStore,
)
from headofcontext.tokens.biscuit.postgres import (
    PostgresRevocationStore,
    RevocationStoreUnavailable,
)
from headofcontext.tokens.mandates import MandateStoreUnavailable, PostgresMandateStore

UNREACHABLE = "postgresql://hoc:hoc@127.0.0.1:1/hoc"


@pytest.fixture(autouse=True)
def fast_checkout() -> None:
    pool.configure(max_size=1, timeout=0.3)
    yield  # type: ignore[misc]
    pool.close_pools()
    pool.configure(max_size=pool.DEFAULT_MAX_SIZE, timeout=pool.CHECKOUT_TIMEOUT_SECONDS)


def test_unreachable_database_is_the_stores_typed_error() -> None:
    with pytest.raises(RevocationStoreUnavailable) as exc:
        PostgresRevocationStore(UNREACHABLE).is_revoked(["x"])
    assert isinstance(exc.value, Unavailable)
    with pytest.raises(AuditUnavailable):
        PostgresAuditSink(UNREACHABLE).count()
    with pytest.raises(ConnectorStale):  # freshness maps its own failure to "stale" (T8)
        PostgresConnectorStateStore(UNREACHABLE, timedelta(hours=1)).assert_fresh()


@pytest.mark.parametrize(
    ("store", "error"),
    [
        (PostgresApprovalStore, ApprovalStoreUnavailable),
        (PostgresMandateStore, MandateStoreUnavailable),
        (PostgresLedger, LedgerUnavailable),
        (PostgresRevocationStore, RevocationStoreUnavailable),
        (PostgresConnectorStateStore, ConnectorStateUnavailable),
        (PostgresAuditSink, AuditUnavailable),
        (PostgresMemoryAdapter, MemoryContentUnavailable),
    ],
)
def test_every_store_declares_an_unavailable_error(store: type, error: type) -> None:
    assert store.unavailable is error and issubclass(error, Unavailable)


def test_approval_store_write_failure(now: datetime | None = None) -> None:
    from headofcontext.actions import ApprovalRequest

    now = now or datetime.now(UTC)
    request = ApprovalRequest(
        request_id="r",
        subject="user:a",
        actor="agent:a",
        delegation_depth=0,
        tool="tool:x",
        args_hash="h",
        decision_id="d",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    with pytest.raises(ApprovalStoreUnavailable):
        PostgresApprovalStore(UNREACHABLE).create(request)


def test_pool_exhaustion_denies_within_the_checkout_timeout() -> None:
    """I5: with every connection busy, a store call fails typed after the timeout; no hang."""
    import time

    from headofcontext.db import pool as pool_module

    pool_module.close_pools()
    pool_module.configure(max_size=1, timeout=0.3)
    p = pool_module.get_pool(UNREACHABLE)  # never connects: the single slot stays unavailable
    started = time.perf_counter()
    with pytest.raises(RevocationStoreUnavailable):
        PostgresRevocationStore(UNREACHABLE).is_revoked(["x"])
    assert time.perf_counter() - started < 3
    p.close()
