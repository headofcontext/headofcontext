"""Decision metrics over the OpenTelemetry metrics API (ADR 0015).

Attributes are outcomes, reasons, action kinds and channel names: bounded sets, no identities.
Without a configured SDK the global provider is a no-op, so this costs nothing in the core.
"""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.metrics import MeterProvider

from headofcontext.core.decision import Decision

_SCOPE = "headofcontext"


class DecisionMetrics:
    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        provider = meter_provider or metrics.get_meter_provider()
        meter = provider.get_meter(_SCOPE)
        self._decisions = meter.create_counter("hoc.decisions", description="Decisions by outcome")
        self._decision_ms = meter.create_histogram(
            "hoc.decision.duration", unit="ms", description="End-to-end decision time"
        )
        self._engine_ms = meter.create_histogram(
            "hoc.engine.duration", unit="ms", description="Time spent in the authorization engine"
        )
        self._notify_failed = meter.create_counter(
            "hoc.approvals.notify_failed", description="Approval channel failures"
        )

    def record(self, decision: Decision, *, total_ms: float, engine_ms: float | None) -> None:
        action = f"{decision.action.kind}:{decision.action.relation}"
        outcome = str(decision.outcome)
        self._decisions.add(
            1, {"hoc.outcome": outcome, "hoc.reason": decision.reason, "hoc.action": action}
        )
        self._decision_ms.record(total_ms, {"hoc.outcome": outcome, "hoc.action": action})
        if engine_ms is not None:
            self._engine_ms.record(engine_ms, {"hoc.action": action})

    def notify_failed(self, channel: str) -> None:
        self._notify_failed.add(1, {"hoc.channel": channel})
