"""Connector framework: snapshots, reconciliation diffs, state and freshness (ADR 0010)."""

from datetime import UTC, datetime, timedelta

import pytest

from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core.errors import ConnectorStale
from headofcontext.read.connectors import (
    DocumentMeta,
    InMemoryConnectorStateStore,
    Snapshot,
    SourceConnector,
    reconcile,
)
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph


class StaticConnector:
    name = "static"

    def __init__(self, tuples: set[tuple[str, str, str]]) -> None:
        self.tuples = tuples

    async def snapshot(self) -> Snapshot:
        return Snapshot(
            connector=self.name,
            tuples=frozenset(self.tuples),
            documents=(DocumentMeta("document:a", "A", "/a", "source:s"),),
            taken_at=datetime.now(UTC),
        )


@pytest.fixture
def state(clock: FrozenClock) -> InMemoryConnectorStateStore:
    return InMemoryConnectorStateStore(max_staleness=timedelta(minutes=10), clock=clock)


async def test_first_sync_writes_everything(
    state: InMemoryConnectorStateStore, clock: FrozenClock
) -> None:
    graph, audit = FakeGraph(), InMemoryAuditSink()
    connector: SourceConnector = StaticConnector(
        {("group:rh#member", "viewer", "source:s"), ("source:s", "parent", "document:a")}
    )
    report = await reconcile(connector, tuples=graph, state=state, audit=audit, clock=clock)
    assert report.added == 2 and report.removed == 0 and report.documents == 1
    assert ("source:s", "parent", "document:a") in graph.tuples
    assert state.last_sync("static") == clock.now()
    assert audit.events[-1].kind is EventKind.CONNECTOR_SYNCED


async def test_second_sync_is_a_diff(
    state: InMemoryConnectorStateStore, clock: FrozenClock
) -> None:
    graph, audit = FakeGraph(), InMemoryAuditSink()
    connector = StaticConnector(
        {("user:alice", "viewer", "document:a"), ("user:bob", "viewer", "document:a")}
    )
    await reconcile(connector, tuples=graph, state=state, audit=audit, clock=clock)
    connector.tuples = {
        ("user:alice", "viewer", "document:a"),
        ("user:carol", "viewer", "document:a"),
    }
    report = await reconcile(connector, tuples=graph, state=state, audit=audit, clock=clock)
    assert report.added == 1 and report.removed == 1
    assert ("user:bob", "viewer", "document:a") not in graph.tuples
    assert ("user:carol", "viewer", "document:a") in graph.tuples
    report = await reconcile(connector, tuples=graph, state=state, audit=audit, clock=clock)
    assert report.added == 0 and report.removed == 0


async def test_failed_write_keeps_state_stale(
    state: InMemoryConnectorStateStore, clock: FrozenClock
) -> None:
    graph, audit = FakeGraph(), InMemoryAuditSink()
    graph.down = True
    connector = StaticConnector({("user:alice", "viewer", "document:a")})
    with pytest.raises(Exception):  # noqa: B017 — EngineUnavailable propagates
        await reconcile(connector, tuples=graph, state=state, audit=audit, clock=clock)
    assert state.last_sync("static") is None
    with pytest.raises(ConnectorStale):
        state.assert_fresh()


async def test_freshness_after_sync(state: InMemoryConnectorStateStore, clock: FrozenClock) -> None:
    graph, audit = FakeGraph(), InMemoryAuditSink()
    await reconcile(StaticConnector(set()), tuples=graph, state=state, audit=audit, clock=clock)
    state.assert_fresh()
    clock.tick(minutes=11)
    with pytest.raises(ConnectorStale):
        state.assert_fresh()


def test_registered_never_synced_is_stale(state: InMemoryConnectorStateStore) -> None:
    state.register("nextcloud")
    with pytest.raises(ConnectorStale):
        state.assert_fresh()
