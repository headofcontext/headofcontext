"""OTLP telemetry wiring (ADR 0015, amended): real providers when HOC_OTEL_ENABLED is set.

Without this, `OtelAuditExporter` and `DecisionMetrics` fall back to the API's no-op providers
and nothing leaves the process. The endpoint comes from ``OTEL_EXPORTER_OTLP_ENDPOINT`` (the
standard variable, read by the exporters themselves) unless given explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


@dataclass(frozen=True, slots=True)
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def build_telemetry(
    *, enabled: bool, service_name: str, endpoint: str | None = None
) -> Telemetry | None:
    if not enabled:
        return None
    resource = Resource.create({SERVICE_NAME: service_name})
    base = endpoint.rstrip("/") if endpoint else None
    span_exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces") if base else OTLPSpanExporter()
    metric_exporter = (
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics") if base else OTLPMetricExporter()
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    meter_provider = MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(metric_exporter)]
    )
    return Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider)


__all__ = ["Telemetry", "build_telemetry"]
