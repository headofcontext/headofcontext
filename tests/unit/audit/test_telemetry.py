"""Telemetry wiring (ADR 0015 amendment): HOC_OTEL_ENABLED configures real OTLP providers."""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from headofcontext.audit.telemetry import Telemetry, build_telemetry


def test_disabled_gives_nothing() -> None:
    assert build_telemetry(enabled=False, service_name="hoc") is None


def test_enabled_wires_otlp_exporters_for_traces_and_metrics() -> None:
    telemetry = build_telemetry(
        enabled=True, service_name="hoc-test", endpoint="http://collector:4318"
    )
    assert isinstance(telemetry, Telemetry)
    processors = telemetry.tracer_provider._active_span_processor._span_processors
    assert any(
        isinstance(p, BatchSpanProcessor) and isinstance(p.span_exporter, OTLPSpanExporter)
        for p in processors
    )
    readers = telemetry.meter_provider._all_metric_readers
    assert any(
        isinstance(r, PeriodicExportingMetricReader) and isinstance(r._exporter, OTLPMetricExporter)
        for r in readers
    )
    resource = telemetry.tracer_provider.resource.attributes
    assert resource["service.name"] == "hoc-test"
    telemetry.shutdown()
