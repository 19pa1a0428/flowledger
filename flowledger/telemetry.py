"""Small OpenTelemetry boundary shared by the API, outbox and worker."""
from collections.abc import Mapping
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


LOGGER = logging.getLogger(__name__)
PROPAGATOR = TraceContextTextMapPropagator()


def configure_telemetry(environment: Mapping[str, str] | None = None) -> bool:
    """Configure OTLP export only when explicitly enabled.

    Invalid optional telemetry configuration is logged and leaves the application
    operational without an exporter. Business behavior must not depend on telemetry.
    """
    environment = os.environ if environment is None else environment
    if environment.get("FLOWLEDGER_OTEL_ENABLED", "false").lower() != "true":
        return False
    endpoint = environment.get("FLOWLEDGER_OTEL_ENDPOINT", "").strip()
    if not endpoint:
        LOGGER.warning("OpenTelemetry disabled: FLOWLEDGER_OTEL_ENDPOINT is missing")
        return False
    try:
        provider = TracerProvider(resource=Resource.create({
            "service.name": environment.get("FLOWLEDGER_OTEL_SERVICE_NAME", "flowledger"),
        }))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
    except Exception:  # optional diagnostics must not prevent service startup
        LOGGER.exception("OpenTelemetry configuration failed; continuing without export")
        return False
    return True


def tracer(name: str) -> Tracer:
    return trace.get_tracer(name)


def current_trace_headers() -> tuple[str | None, str | None]:
    carrier: dict[str, str] = {}
    PROPAGATOR.inject(carrier)
    return carrier.get("traceparent"), carrier.get("tracestate")


def extracted_context(traceparent: str | None, tracestate: str | None):
    carrier = {}
    if traceparent is not None:
        carrier["traceparent"] = traceparent
    if tracestate is not None:
        carrier["tracestate"] = tracestate
    return PROPAGATOR.extract(carrier)


# Each API or worker process imports this module once. Export remains opt-in.
EXPORT_CONFIGURED = configure_telemetry()
