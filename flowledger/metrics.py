"""Fail-safe, low-cardinality OpenTelemetry metrics for modular processes."""
from collections.abc import Callable, Mapping
from math import isfinite
import logging
import os
from time import monotonic

from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from flowledger.claims import RedisClaimQueue
from flowledger.operations import health_snapshot
from flowledger.repository import SessionFactory


LOGGER = logging.getLogger(__name__)
RELAY_OUTCOMES = frozenset({"submitted", "replayed", "error"})
WORKER_OUTCOMES = frozenset({
    "completed", "completed_ack_expired", "retry_scheduled",
    "quarantined_conflict", "quarantined_malformed", "error",
})


def configure_metrics(environment: Mapping[str, str] | None = None) -> bool:
    """Install OTLP metric export only when explicitly and validly enabled."""
    environment = os.environ if environment is None else environment
    if environment.get("FLOWLEDGER_OTEL_METRICS_ENABLED", "false").lower() != "true":
        return False
    endpoint = environment.get("FLOWLEDGER_OTEL_METRICS_ENDPOINT", "").strip()
    if not endpoint:
        LOGGER.warning("OpenTelemetry metrics disabled: endpoint is missing")
        return False
    try:
        interval_ms = int(environment.get("FLOWLEDGER_OTEL_METRICS_EXPORT_INTERVAL_MS", "30000"))
        if not 1_000 <= interval_ms <= 3_600_000:
            raise ValueError("metrics export interval must be between 1000 and 3600000 ms")
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint), export_interval_millis=interval_ms,
        )
        provider = MeterProvider(
            resource=Resource.create({
                "service.name": environment.get(
                    "FLOWLEDGER_OTEL_SERVICE_NAME", "flowledger"
                ),
            }),
            metric_readers=[reader],
        )
        otel_metrics.set_meter_provider(provider)
    except Exception:  # diagnostics must not prevent service startup
        LOGGER.exception("OpenTelemetry metrics configuration failed; continuing without export")
        return False
    return True


class FlowLedgerMetrics:
    """Metric recording boundary that absorbs SDK/exporter-facing failures."""

    def __init__(self, meter: Meter, *, enabled: bool):
        self.enabled = enabled
        self._relay = meter.create_counter(
            "flowledger.relay.commands", unit="{command}",
            description="Outbox relay command outcomes",
        )
        self._worker = meter.create_counter(
            "flowledger.worker.commands", unit="{command}",
            description="Worker command outcomes",
        )
        self._maintenance = meter.create_counter(
            "flowledger.worker.maintenance", unit="{command}",
            description="Recovered and promoted commands",
        )
        self._queue_depth = meter.create_gauge(
            "flowledger.queue.depth", unit="{command}",
            description="Capped Redis queue cardinality",
        )
        self._outbox_pending = meter.create_gauge(
            "flowledger.outbox.pending", unit="{command}",
            description="Undelivered database outbox commands",
        )
        self._outbox_age = meter.create_gauge(
            "flowledger.outbox.oldest_age", unit="s",
            description="Age of the oldest undelivered outbox command",
        )

    def relay(self, outcome: str) -> None:
        if outcome not in RELAY_OUTCOMES:
            raise ValueError("Unsupported relay metric outcome")
        self._safe(self._relay.add, 1, {"outcome": outcome})

    def worker(self, outcome: str) -> None:
        if outcome not in WORKER_OUTCOMES:
            raise ValueError("Unsupported worker metric outcome")
        self._safe(self._worker.add, 1, {"outcome": outcome})

    def maintenance(self, *, recovered: int, promoted: int) -> None:
        if recovered:
            self._safe(self._maintenance.add, recovered, {"operation": "recovered"})
        if promoted:
            self._safe(self._maintenance.add, promoted, {"operation": "promoted"})

    def capture_health(self, sessions: SessionFactory, queue: RedisClaimQueue) -> None:
        """Record a payload-free snapshot; source or SDK errors remain diagnostic only."""
        if not self.enabled:
            return
        try:
            snapshot = health_snapshot(sessions, queue)
        except Exception:
            LOGGER.exception("OpenTelemetry health metric collection failed")
            return
        self._safe(self._outbox_pending.set, snapshot.outbox.pending)
        self._safe(
            self._outbox_age.set,
            snapshot.outbox.oldest_age_seconds or 0,
            {"empty": snapshot.outbox.oldest_age_seconds is None},
        )
        for state in ("pending", "claimed", "delayed", "quarantined", "exhausted"):
            count = getattr(snapshot.redis, state)
            self._safe(
                self._queue_depth.set, count.value,
                {"state": state, "capped": count.capped},
            )

    def _safe(self, operation, *args) -> None:
        if not self.enabled:
            return
        try:
            operation(*args)
        except Exception:
            LOGGER.exception("OpenTelemetry metric recording failed")


class HealthMetricSampler:
    """Bound health collection frequency independently of busy work cycles."""

    def __init__(self, sink: FlowLedgerMetrics, sessions: SessionFactory,
                 queue: RedisClaimQueue, *, interval_seconds: float = 30.0,
                 clock: Callable[[], float] = monotonic):
        if (type(interval_seconds) not in (int, float)
                or not isfinite(interval_seconds)
                or not 1 <= interval_seconds <= 3_600):
            raise ValueError("Metric interval must be between 1 and 3600 seconds")
        self._sink = sink
        self._sessions = sessions
        self._queue = queue
        self._interval = float(interval_seconds)
        self._clock = clock
        self._next_capture = 0.0

    def capture_if_due(self) -> bool:
        if not self._sink.enabled:
            return False
        now = self._clock()
        if now < self._next_capture:
            return False
        self._next_capture = now + self._interval
        self._sink.capture_health(self._sessions, self._queue)
        return True


METRICS_CONFIGURED = configure_metrics()
APPLICATION_METRICS = FlowLedgerMetrics(
    otel_metrics.get_meter("flowledger.operations"), enabled=METRICS_CONFIGURED,
)
