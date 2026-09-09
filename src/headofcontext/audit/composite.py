from __future__ import annotations

from collections.abc import Sequence

from headofcontext.core.events import AuditEvent, AuditSink


class CompositeAuditSink:
    """Records into every sink in order; the first failure aborts and propagates (fail closed)."""

    def __init__(self, sinks: Sequence[AuditSink]) -> None:
        if not sinks:
            raise ValueError("at least one sink is required")
        self._sinks = tuple(sinks)

    def record(self, event: AuditEvent) -> None:
        for sink in self._sinks:
            sink.record(event)
