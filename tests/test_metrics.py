from unittest.mock import Mock, patch
import unittest

from flowledger.claims import BoundedCount, QueueHealth
from flowledger.metrics import FlowLedgerMetrics, HealthMetricSampler, configure_metrics
from flowledger.operations import OperationalHealth, OutboxHealth


class FakeMeter:
    def __init__(self):
        self.instruments = {}

    def create_counter(self, name, **_kwargs):
        return self._instrument(name)

    def create_gauge(self, name, **_kwargs):
        return self._instrument(name)

    def _instrument(self, name):
        instrument = Mock(name=name)
        self.instruments[name] = instrument
        return instrument


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.meter = FakeMeter()
        self.metrics = FlowLedgerMetrics(self.meter, enabled=True)

    def test_outcomes_have_only_fixed_low_cardinality_attributes(self):
        self.metrics.relay("submitted")
        self.metrics.worker("retry_scheduled")
        self.metrics.maintenance(recovered=2, promoted=3)

        self.meter.instruments["flowledger.relay.commands"].add.assert_called_once_with(
            1, {"outcome": "submitted"}
        )
        self.meter.instruments["flowledger.worker.commands"].add.assert_called_once_with(
            1, {"outcome": "retry_scheduled"}
        )
        self.assertEqual(
            self.meter.instruments["flowledger.worker.maintenance"].add.call_args_list,
            [unittest.mock.call(2, {"operation": "recovered"}),
             unittest.mock.call(3, {"operation": "promoted"})],
        )
        with self.assertRaises(ValueError):
            self.metrics.worker("workflow-123")

    def test_health_snapshot_records_payload_free_bounded_gauges(self):
        snapshot = OperationalHealth(
            OutboxHealth(pending=4, oldest_age_seconds=17),
            QueueHealth(
                pending=BoundedCount(5, False), claimed=BoundedCount(6, True),
                delayed=BoundedCount(7, False), quarantined=BoundedCount(8, False),
                exhausted=BoundedCount(9, True),
                count_limit=10,
            ),
        )
        with patch("flowledger.metrics.health_snapshot", return_value=snapshot) as source:
            self.metrics.capture_health(Mock(), Mock())

        source.assert_called_once()
        self.meter.instruments["flowledger.outbox.pending"].set.assert_called_once_with(4)
        self.meter.instruments["flowledger.outbox.oldest_age"].set.assert_called_once_with(
            17, {"empty": False}
        )
        depth_calls = self.meter.instruments["flowledger.queue.depth"].set.call_args_list
        self.assertEqual([call.args[0] for call in depth_calls], [5, 6, 7, 8, 9])
        self.assertEqual(
            [call.args[1]["state"] for call in depth_calls],
            ["pending", "claimed", "delayed", "quarantined", "exhausted"],
        )
        self.assertEqual([call.args[1]["capped"] for call in depth_calls],
                         [False, True, False, False, True])

    def test_disabled_metrics_do_not_query_health_sources(self):
        disabled = FlowLedgerMetrics(FakeMeter(), enabled=False)
        with patch("flowledger.metrics.health_snapshot") as source:
            disabled.capture_health(Mock(), Mock())
        source.assert_not_called()

    def test_source_and_instrument_failures_are_absorbed(self):
        self.meter.instruments["flowledger.worker.commands"].add.side_effect = RuntimeError
        with self.assertLogs("flowledger.metrics", level="ERROR"):
            self.metrics.worker("completed")
        with patch("flowledger.metrics.health_snapshot", side_effect=ConnectionError):
            with self.assertLogs("flowledger.metrics", level="ERROR"):
                self.metrics.capture_health(Mock(), Mock())

    def test_sampler_is_frequency_bounded_and_disabled_is_inert(self):
        sink = Mock(enabled=True)
        clock = Mock(side_effect=[10.0, 11.0, 15.0])
        sampler = HealthMetricSampler(sink, "sessions", "queue", interval_seconds=5,
                                      clock=clock)
        self.assertTrue(sampler.capture_if_due())
        self.assertFalse(sampler.capture_if_due())
        self.assertTrue(sampler.capture_if_due())
        self.assertEqual(sink.capture_health.call_args_list,
                         [unittest.mock.call("sessions", "queue")] * 2)

        sink.enabled = False
        self.assertFalse(sampler.capture_if_due())
        self.assertEqual(clock.call_count, 3)

    def test_metrics_require_explicit_valid_configuration(self):
        self.assertFalse(configure_metrics({}))
        with self.assertLogs("flowledger.metrics", level="WARNING"):
            self.assertFalse(configure_metrics({"FLOWLEDGER_OTEL_METRICS_ENABLED": "true"}))
        with self.assertLogs("flowledger.metrics", level="ERROR"):
            self.assertFalse(configure_metrics({
                "FLOWLEDGER_OTEL_METRICS_ENABLED": "true",
                "FLOWLEDGER_OTEL_METRICS_ENDPOINT": "http://collector/v1/metrics",
                "FLOWLEDGER_OTEL_METRICS_EXPORT_INTERVAL_MS": "999",
            }))

    def test_sampler_rejects_invalid_or_unbounded_intervals(self):
        for interval in (True, 0, 3601, float("inf"), float("nan")):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    HealthMetricSampler(Mock(), Mock(), Mock(), interval_seconds=interval)


if __name__ == "__main__":
    unittest.main()
