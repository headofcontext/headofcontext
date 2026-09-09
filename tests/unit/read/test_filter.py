"""read.filter_items: only what the subject may view reaches the model (ADR 0010)."""

from dataclasses import dataclass

import pytest

from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Kind, PrincipalChain, Scope
from headofcontext.read import LIST_OBJECTS_THRESHOLD, FilterResult, filter_items
from tests.unit.memory.conftest import FakeGraph

READ_ALL = Scope.of(Capability(Kind.READ, "document:*"))
ALICE = PrincipalChain.root("user:alice", "agent:a", READ_ALL)


@dataclass
class Hit:
    id: str
    score: float


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    for i in range(3):
        g.grant("user:alice", "viewer", f"document:ok-{i}")
    return g


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


async def test_keeps_only_visible_in_index_order(
    graph: FakeGraph, audit: InMemoryAuditSink
) -> None:
    items = [
        {"id": "document:ok-2"},
        {"id": "document:secret"},
        {"id": "document:ok-0"},
        {"id": "document:ok-1"},
    ]
    result = await filter_items(ALICE, items, engine=graph, audit=audit)
    assert isinstance(result, FilterResult)
    assert [i["id"] for i in result.kept] == ["document:ok-2", "document:ok-0", "document:ok-1"]
    assert result.dropped == ("document:secret",)
    assert result.kept_count == 3 and result.dropped_count == 1


async def test_objects_with_id_attribute(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    hits = [Hit("document:ok-0", 0.9), Hit("document:nope", 0.8)]
    result = await filter_items(ALICE, hits, engine=graph, audit=audit)
    assert [h.id for h in result.kept] == ["document:ok-0"]


async def test_custom_id_of(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    hits = [{"doc": "ok-0", "text": "..."}, {"doc": "nope", "text": "..."}]
    result = await filter_items(
        ALICE, hits, engine=graph, audit=audit, id_of=lambda h: f"document:{h['doc']}"
    )
    assert [h["doc"] for h in result.kept] == ["ok-0"]


async def test_items_without_valid_reference_are_dropped(
    graph: FakeGraph, audit: InMemoryAuditSink
) -> None:
    items = [
        {"id": "document:ok-0"},
        {"id": ""},
        {"nope": 1},
        {"id": "ok-1"},
        {"id": 'document:ok-0"); x'},
        {"id": "tool:x"},
    ]
    result = await filter_items(ALICE, items, engine=graph, audit=audit)
    assert [i["id"] for i in result.kept] == ["document:ok-0"]
    assert result.dropped_count == 5


async def test_scope_applies_before_engine(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    narrow = PrincipalChain.root(
        "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:hr/*"))
    )
    result = await filter_items(narrow, [{"id": "document:ok-0"}], engine=graph, audit=audit)
    assert result.kept == []


async def test_engine_down_returns_nothing(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    graph.down = True
    result = await filter_items(ALICE, [{"id": "document:ok-0"}], engine=graph, audit=audit)
    assert result.kept == [] and result.dropped == ("document:ok-0",)


async def test_single_audit_event_by_default(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    await filter_items(
        ALICE, [{"id": "document:ok-0"}, {"id": "document:secret"}], engine=graph, audit=audit
    )
    assert [e.kind for e in audit.events] == [EventKind.READ_FILTERED]
    event = audit.events[0]
    assert (
        event.subject == "user:alice"
        and event.outcome == "ALLOW"
        and "kept=1" in event.reason
        and "dropped=1" in event.reason
    )
    assert "secret" not in event.canonical_json()


async def test_per_item_audit(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    await filter_items(
        ALICE,
        [{"id": "document:ok-0"}, {"id": "document:secret"}],
        engine=graph,
        audit=audit,
        per_item_audit=True,
    )
    kinds = [e.kind for e in audit.events]
    assert kinds.count(EventKind.DECISION) == 2 and kinds[-1] is EventKind.READ_FILTERED


async def test_list_objects_path_agrees_with_batch(
    graph: FakeGraph, audit: InMemoryAuditSink
) -> None:
    for i in range(LIST_OBJECTS_THRESHOLD + 10):
        graph.grant("user:alice", "viewer", f"document:big-{i}")
    items = [{"id": f"document:big-{i}"} for i in range(LIST_OBJECTS_THRESHOLD + 10)] + [
        {"id": "document:secret"}
    ]
    via_list = await filter_items(ALICE, items, engine=graph, audit=audit)
    via_batch = await filter_items(ALICE, items, engine=graph, audit=audit, strategy="batch")
    assert [i["id"] for i in via_list.kept] == [i["id"] for i in via_batch.kept]
    assert via_list.strategy == "list_objects" and via_batch.strategy == "batch"
    assert via_list.dropped == ("document:secret",)


async def test_duplicates_kept_as_given(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    items = [{"id": "document:ok-0"}, {"id": "document:ok-0"}]
    result = await filter_items(ALICE, items, engine=graph, audit=audit)
    assert len(result.kept) == 2


async def test_empty(graph: FakeGraph, audit: InMemoryAuditSink) -> None:
    result = await filter_items(ALICE, [], engine=graph, audit=audit)
    assert result.kept == [] and audit.events == []
