from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from flowledger.alerting import (
    MAX_SAMPLES, AlertPolicy, AlertSample, AlertStatus, AlertTransition,
    evaluate, main, parse_samples, read_samples,
)


START = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)


def sample(offset: int, age: int | None = 0, quarantined: int = 0,
           exhausted: int = 0) -> AlertSample:
    return AlertSample(START + timedelta(minutes=offset), age,
                       quarantined, exhausted)


class AlertPolicyTests(TestCase):
    def test_outbox_requires_sustained_breach_and_recovery(self):
        results = evaluate((
            sample(0, 300), sample(1, 301), sample(2, 302),
            sample(3, 0), sample(4, None),
        ), AlertPolicy(outbox_age_seconds=300, trigger_samples=3,
                       recovery_samples=2))
        decisions = [result.alerts[0] for result in results]

        self.assertEqual([item.transition for item in decisions], [
            AlertTransition.NONE, AlertTransition.NONE, AlertTransition.FIRED,
            AlertTransition.NONE, AlertTransition.RECOVERED,
        ])
        self.assertEqual([item.status for item in decisions], [
            AlertStatus.CLEAR, AlertStatus.CLEAR, AlertStatus.FIRING,
            AlertStatus.FIRING, AlertStatus.CLEAR,
        ])

    def test_intermittent_breach_does_not_fire(self):
        results = evaluate((sample(0, 500), sample(1, 0), sample(2, 500)),
                           AlertPolicy(trigger_samples=2))
        self.assertTrue(all(result.alerts[0].status is AlertStatus.CLEAR
                            for result in results))

    def test_terminal_growth_uses_positive_deltas_and_has_own_state(self):
        results = evaluate((
            sample(0, quarantined=5, exhausted=5),
            sample(1, quarantined=7, exhausted=5),
            sample(2, quarantined=6, exhausted=8),
            sample(3, quarantined=4, exhausted=10),
        ), AlertPolicy(terminal_growth=2, trigger_samples=2, recovery_samples=1))

        self.assertEqual([result.terminal_growth for result in results], [0, 2, 2, 0])
        self.assertEqual(results[2].alerts[1].transition, AlertTransition.FIRED)
        self.assertEqual(results[3].alerts[1].transition, AlertTransition.RECOVERED)
        self.assertTrue(all(result.alerts[0].status is AlertStatus.CLEAR
                            for result in results))

    def test_timestamp_and_numeric_contracts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            evaluate((sample(0), sample(0)), AlertPolicy())
        with self.assertRaisesRegex(ValueError, "timezone"):
            AlertSample(datetime(2026, 11, 12), 0, 0, 0)
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    AlertSample(START, value, 0, 0)
        with self.assertRaisesRegex(ValueError, "at least one"):
            evaluate((), AlertPolicy())

    def test_policy_and_input_bounds_are_enforced(self):
        for kwargs in ({"trigger_samples": 0}, {"recovery_samples": True},
                       {"terminal_growth": 86_401}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    AlertPolicy(**kwargs)
        with self.assertRaisesRegex(ValueError, "at most"):
            evaluate(tuple(sample(i) for i in range(MAX_SAMPLES + 1)), AlertPolicy())

    def test_parser_rejects_non_array_and_malformed_samples(self):
        with self.assertRaisesRegex(ValueError, "JSON array"):
            parse_samples({})
        with self.assertRaisesRegex(ValueError, "invalid observed_at"):
            parse_samples([{"observed_at": "bad", "quarantined": 0,
                            "exhausted": 0}])
        with self.assertRaisesRegex(ValueError, "quarantined must be an integer"):
            parse_samples([{"observed_at": START.isoformat(), "quarantined": True,
                            "exhausted": 0}])

    def test_cli_emits_deterministic_json_without_side_effects(self):
        payload = [
            {"observed_at": (START + timedelta(minutes=i)).isoformat(),
             "oldest_outbox_age_seconds": 10, "quarantined": 0,
             "exhausted": 0}
            for i in range(2)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-alerts.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path), "--outbox-age-seconds", "10",
                                       "--trigger-samples", "2"]), 0)
            rendered = json.loads(output.getvalue())
            self.assertEqual(rendered[-1]["alerts"][0]["transition"], "fired")
            self.assertEqual(rendered[-1]["observed_at"], payload[-1]["observed_at"])

    def test_file_size_limit_is_checked_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b" " * 1_000_001)
            with self.assertRaisesRegex(ValueError, "must not exceed"):
                read_samples(path)
