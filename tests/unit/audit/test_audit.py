from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from headofcontext.audit import (
    AuditEvent,
    CompositeAuditSink,
    EventKind,
    InMemoryAuditSink,
    OtelAuditExporter,
    StoredAuditEvent,
    compute_hash,
)
from headofcontext.core.errors import AuditUnavailable


def event(**overrides: object) -> AuditEvent:
    base = dict(
        kind=EventKind.DECISION,
        timestamp=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        subject="user:alice",
        actor="agent:a",
        delegation_depth=0,
        action="read:viewer",
        resource="document:x",
        outcome="ALLOW",
        reason="allowed",
        decision_id="d1",
    )
    base.update(overrides)
    return AuditEvent(**base)  # type: ignore[arg-type]


class TestHashChain:
    def test_chain_verifies(self) -> None:
        sink = InMemoryAuditSink()
        for i in range(5):
            sink.record(event(resource=f"document:{i}"))
        assert sink.verify_chain()
        assert sink.stored[0].prev_hash == "0" * 64
        assert sink.stored[1].prev_hash == sink.stored[0].hash

    def test_tamper_detected(self) -> None:
        sink = InMemoryAuditSink()
        sink.record(event())
        sink.record(event(resource="document:y"))
        forged = StoredAuditEvent(
            event=event(resource="document:z", event_id=sink.events[0].event_id),
            prev_hash=sink.stored[0].prev_hash,
            hash=sink.stored[0].hash,
        )
        sink._events[0] = forged
        assert not sink.verify_chain()

    def test_canonical_json_is_deterministic(self) -> None:
        a, b = event(event_id="e1"), event(event_id="e1")
        assert a.canonical_json() == b.canonical_json()
        assert compute_hash("0" * 64, a) == compute_hash("0" * 64, b)


class TestComposite:
    def test_records_into_all(self) -> None:
        a, b = InMemoryAuditSink(), InMemoryAuditSink()
        CompositeAuditSink([a, b]).record(event())
        assert len(a.events) == 1 and len(b.events) == 1

    def test_failure_propagates(self) -> None:
        class Broken:
            def record(self, e: AuditEvent) -> None:
                raise AuditUnavailable("down")

        with pytest.raises(AuditUnavailable):
            CompositeAuditSink([Broken(), InMemoryAuditSink()]).record(event())

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            CompositeAuditSink([])


class TestOtel:
    def test_span_attributes(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        sink = OtelAuditExporter(provider)
        sink.record(event(token_fingerprint="abc", args_hash="def"))
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "hoc.audit.decision"
        attrs = dict(span.attributes or {})
        assert attrs["gen_ai.agent.id"] == "agent:a"
        assert attrs["gen_ai.operation.name"] == "read:viewer"
        assert attrs["hoc.subject"] == "user:alice"
        assert attrs["hoc.decision.outcome"] == "ALLOW"
        assert attrs["hoc.token.fingerprint"] == "abc"
        assert attrs["hoc.args.hash"] == "def"
