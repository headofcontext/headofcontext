"""Export audit events as OpenTelemetry spans with GenAI semantic conventions (ADR 0005)."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as gen_ai
from opentelemetry.trace import SpanKind, TracerProvider

from headofcontext.core.events import AuditEvent

# HeadOfContext-specific attributes; the gen_ai.* ones come from the semantic conventions.
ATTR_SUBJECT = "hoc.subject"
ATTR_DELEGATION_DEPTH = "hoc.delegation_depth"
ATTR_RESOURCE = "hoc.resource"
ATTR_OUTCOME = "hoc.decision.outcome"
ATTR_REASON = "hoc.decision.reason"
ATTR_DECISION_ID = "hoc.decision.id"
ATTR_ENGINE_LATENCY = "hoc.engine.latency_ms"
ATTR_TOKEN_FINGERPRINT = "hoc.token.fingerprint"  # noqa: S105
ATTR_ARGS_HASH = "hoc.args.hash"
ATTR_EVENT_KIND = "hoc.event.kind"
ATTR_EVENT_ID = "hoc.event.id"


class OtelAuditExporter:
    """A secondary AuditSink: one span per event. Never the only sink (spans can be sampled)."""

    def __init__(self, tracer_provider: TracerProvider | None = None) -> None:
        provider = tracer_provider or trace.get_tracer_provider()
        self._tracer = provider.get_tracer("headofcontext.audit")

    def record(self, event: AuditEvent) -> None:
        attributes: dict[str, str | int | float] = {
            gen_ai.GEN_AI_AGENT_ID: event.actor,
            gen_ai.GEN_AI_OPERATION_NAME: event.action,
            ATTR_EVENT_KIND: str(event.kind),
            ATTR_EVENT_ID: event.event_id,
            ATTR_SUBJECT: event.subject,
            ATTR_DELEGATION_DEPTH: event.delegation_depth,
            ATTR_RESOURCE: event.resource,
            ATTR_OUTCOME: event.outcome,
            ATTR_REASON: event.reason,
            ATTR_ENGINE_LATENCY: event.engine_latency_ms,
        }
        if event.decision_id is not None:
            attributes[ATTR_DECISION_ID] = event.decision_id
        if event.token_fingerprint is not None:
            attributes[ATTR_TOKEN_FINGERPRINT] = event.token_fingerprint
        if event.args_hash is not None:
            attributes[ATTR_ARGS_HASH] = event.args_hash
        start_ns = int(event.timestamp.timestamp() * 1_000_000_000)
        span = self._tracer.start_span(
            f"hoc.audit.{event.kind}",
            kind=SpanKind.INTERNAL,
            attributes=attributes,
            start_time=start_ns,
        )
        span.end(end_time=start_ns)
