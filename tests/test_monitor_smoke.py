from unittest import TestCase

from scripts.monitor_smoke import verify_capped_failure, verify_incident


def sample(growth: int, transition: str = "none") -> dict:
    return {
        "event": "sample",
        "terminal_growth": growth,
        "alerts": [
            {"name": "oldest_outbox_age", "transition": "none"},
            {"name": "terminal_queue_growth", "transition": transition},
        ],
    }


class MonitorSmokeTests(TestCase):
    def test_incident_requires_exact_growth_firing_and_recovery(self):
        verify_incident([
            sample(0), sample(1), sample(1, "fired"),
            sample(0), sample(0, "recovered"),
        ])

        broken = [sample(0), sample(1), sample(1), sample(0), sample(0, "recovered")]
        with self.assertRaisesRegex(RuntimeError, "did not fire"):
            verify_incident(broken)

    def test_capped_failure_requires_explicit_error_and_nonzero_exit(self):
        events = [
            {"event": "sample_error", "error_type": "CappedTerminalCounts"},
            {"event": "stopped", "reason": "consecutive_sample_errors"},
        ]
        verify_capped_failure(events, 1)

        with self.assertRaisesRegex(RuntimeError, "status 1"):
            verify_capped_failure(events, 0)
        with self.assertRaisesRegex(RuntimeError, "not reported"):
            verify_capped_failure([
                {"event": "sample_error", "error_type": "ConnectionError"}, events[1],
            ], 1)
