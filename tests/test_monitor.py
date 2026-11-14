from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, patch

from flowledger.alerting import AlertPolicy
from flowledger.claims import BoundedCount, QueueHealth
from flowledger.monitor import MonitorConfig, run_monitor
from flowledger.operations import OperationalHealth, OutboxHealth


START = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)


def health(age: int | None = 0, quarantined: int = 0, exhausted: int = 0,
           *, capped: bool = False, pending: int = 0) -> OperationalHealth:
    exact_zero = BoundedCount(0, False)
    return OperationalHealth(
        OutboxHealth(pending, age),
        QueueHealth(10_000, exact_zero, exact_zero, exact_zero,
                    BoundedCount(quarantined, capped),
                    BoundedCount(exhausted, capped)),
    )


def config(*, max_samples: int = 0, max_errors: int = 3,
           policy: AlertPolicy | None = None) -> MonitorConfig:
    return MonitorConfig("sqlite://", "redis://synthetic", interval_seconds=1,
                         max_consecutive_errors=max_errors,
                         max_samples=max_samples,
                         policy=policy or AlertPolicy())


class MonitorTests(TestCase):
    def test_monitor_preserves_policy_state_and_emits_transitions(self):
        snapshots = Mock(side_effect=[
            health(300, pending=1), health(301, pending=1), health(302, pending=1),
            health(0), health(None),
        ])
        times = iter(START + timedelta(minutes=index) for index in range(5))
        events = []

        result = run_monitor(
            snapshots, Event(),
            config(max_samples=5, policy=AlertPolicy(outbox_age_seconds=300)),
            emit=events.append, clock=lambda: next(times), wait=lambda _seconds: False,
        )

        self.assertEqual(result, 0)
        self.assertEqual([event["event"] for event in events],
                         ["sample"] * 5 + ["stopped"])
        self.assertEqual(events[2]["alerts"][0]["transition"], "fired")
        self.assertEqual(events[4]["alerts"][0]["transition"], "recovered")
        self.assertEqual(events[-1], {
            "attempts": 5, "errors": 0, "event": "stopped",
            "reason": "max_samples", "samples": 5,
        })

    def test_transient_sampling_error_does_not_advance_or_clear_alert_state(self):
        snapshots = Mock(side_effect=[health(500), RuntimeError("private detail"),
                                     health(500)])
        times = iter(START + timedelta(minutes=index) for index in range(3))
        events = []

        result = run_monitor(
            snapshots, Event(),
            config(max_samples=3, policy=AlertPolicy(trigger_samples=2)),
            emit=events.append, clock=lambda: next(times), wait=lambda _seconds: False,
        )

        self.assertEqual(result, 0)
        self.assertEqual([event["event"] for event in events],
                         ["sample", "sample_error", "sample", "stopped"])
        self.assertEqual(events[1]["error_type"], "RuntimeError")
        self.assertNotIn("private detail", repr(events))
        self.assertEqual(events[2]["alerts"][0]["transition"], "fired")

    def test_consecutive_failures_stop_with_nonzero_status(self):
        snapshots = Mock(side_effect=ConnectionError("synthetic outage"))
        times = iter(START + timedelta(minutes=index) for index in range(2))
        events = []

        result = run_monitor(
            snapshots, Event(), config(max_errors=2), emit=events.append,
            clock=lambda: next(times), wait=lambda _seconds: False,
        )

        self.assertEqual(result, 1)
        self.assertEqual(events[-1]["reason"], "consecutive_sample_errors")
        self.assertEqual(events[-1]["errors"], 2)

    def test_capped_terminal_counts_are_explicit_sampling_errors(self):
        events = []

        result = run_monitor(
            Mock(return_value=health(quarantined=10_000, capped=True)),
            Event(), config(max_errors=1), emit=events.append,
            clock=lambda: START, wait=lambda _seconds: False,
        )

        self.assertEqual(result, 1)
        self.assertEqual(events[0]["error_type"], "CappedTerminalCounts")

    def test_preexisting_stop_is_graceful_and_reads_no_services(self):
        stop = Event()
        stop.set()
        snapshots = Mock()
        events = []

        self.assertEqual(run_monitor(snapshots, stop, config(), emit=events.append), 0)
        snapshots.assert_not_called()
        self.assertEqual(events, [{
            "attempts": 0, "errors": 0, "event": "stopped",
            "reason": "signal", "samples": 0,
        }])

    def test_environment_configuration_is_bounded_and_fail_closed(self):
        environment = {
            "FLOWLEDGER_DATABASE_URL": "sqlite://",
            "FLOWLEDGER_REDIS_URL": "redis://synthetic",
            "FLOWLEDGER_MONITOR_INTERVAL_SECONDS": "5",
            "FLOWLEDGER_MONITOR_MAX_SAMPLES": "7",
            "FLOWLEDGER_ALERT_TRIGGER_SAMPLES": "2",
        }
        with patch.dict("os.environ", environment, clear=True):
            result = MonitorConfig.from_environment()
        self.assertEqual((result.interval_seconds, result.max_samples,
                          result.policy.trigger_samples), (5.0, 7, 2))

        environment["FLOWLEDGER_MONITOR_INTERVAL_SECONDS"] = "0"
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ValueError, "monitor interval"):
                MonitorConfig.from_environment()

    def test_compose_monitor_is_profiled_and_has_no_endpoint(self):
        compose = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
        block = compose.split("  monitor:", 1)[1].split("\nvolumes:", 1)[0]
        self.assertIn('profiles: ["operations"]', block)
        self.assertIn('command: ["python", "-m", "flowledger.monitor"]', block)
        self.assertNotIn("ports:", block)
