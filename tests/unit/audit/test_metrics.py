"""Decision metrics (ADR 0015): counts and latencies, never identities."""

from __future__ import annotations

import json
from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from headofcontext.audit import InMemoryAuditSink
from headofcontext.audit.metrics import DecisionMetrics
from headofcontext.core import Action, Capability, Decider, Kind, PrincipalChain, Scope
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

CHAIN = PrincipalChain.root(
    "user:alice", "agent:assistant", Scope.of(Capability(Kind.READ, "document:*"))
)


def collect(reader: InMemoryMetricReader) -> dict[str, list[dict[str, Any]]]:
    data = reader.get_metrics_data()
    out: dict[str, list[dict[str, Any]]] = {}
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for point in metric.data.data_points:
                    entry: dict[str, Any] = {"attributes": dict(point.attributes or {})}
                    entry["value"] = getattr(point, "value", None)
                    entry["count"] = getattr(point, "count", None)
                    out.setdefault(metric.name, []).append(entry)
    return out


def make(clock: FrozenClock) -> tuple[Decider, InMemoryMetricReader, FakeGraph]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    graph = FakeGraph()
    graph.grant("user:alice", "viewer", "document:a")
    decider = Decider(
        graph, InMemoryAuditSink(), clock, metrics=DecisionMetrics(meter_provider=provider)
    )
    return decider, reader, graph


async def test_decide_records_counter_and_histograms(clock: FrozenClock) -> None:
    decider, reader, _ = make(clock)
    await decider.decide(CHAIN, Action.read(), "document:a")
    await decider.decide(CHAIN, Action.read(), "document:b")
    await decider.decide(CHAIN, Action.invoke(), "tool:x")  # scope excluded, no engine call
    metrics = collect(reader)

    counts = {
        (p["attributes"]["hoc.outcome"], p["attributes"]["hoc.reason"]): p["value"]
        for p in metrics["hoc.decisions"]
    }
    assert counts[("ALLOW", "allowed")] == 1
    assert sum(v for (o, _), v in counts.items() if o == "DENY") == 2
    assert sum(p["count"] for p in metrics["hoc.decision.duration"]) == 3
    assert sum(p["count"] for p in metrics["hoc.engine.duration"]) == 2, (
        "scope denials skip the engine"
    )


async def test_batch_paths_record_one_point_per_item(clock: FrozenClock) -> None:
    decider, reader, _ = make(clock)
    await decider.decide_each(CHAIN, Action.read(), ["document:a", "document:b", "document:c"])
    metrics = collect(reader)
    assert sum(p["value"] for p in metrics["hoc.decisions"]) == 3


async def test_attributes_never_carry_identities(clock: FrozenClock) -> None:
    decider, reader, _ = make(clock)
    await decider.decide(CHAIN, Action.read(), "document:a")
    dumped = json.dumps(collect(reader), default=str)
    for secret in ("alice", "document:a", "agent:assistant"):
        assert secret not in dumped
    keys = {k for points in collect(reader).values() for p in points for k in p["attributes"]}
    assert keys <= {"hoc.outcome", "hoc.reason", "hoc.action"}


def test_default_metrics_are_a_noop_without_sdk() -> None:
    DecisionMetrics().notify_failed("webhook")  # global provider defaults to no-op
