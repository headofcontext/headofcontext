"""What the decider needs from a metrics implementation (ADR 0023)."""

from __future__ import annotations

from typing import Protocol

from headofcontext.core.decision import Decision


class MetricsSink(Protocol):
    def record(self, decision: Decision, *, total_ms: float, engine_ms: float | None) -> None: ...

    def notify_failed(self, channel: str) -> None: ...


__all__ = ["MetricsSink"]
