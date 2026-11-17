import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import Mock

from jsonschema import Draft202012Validator, FormatChecker

from flowledger.monitor import MONITOR_EVENT_SCHEMA_VERSION, MonitorConfig, run_monitor
from tests.test_monitor import health


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "monitor-event-v1.schema.json").read_text(encoding="utf-8")
)
COMPATIBILITY_EVENTS = json.loads(
    (ROOT / "tests" / "fixtures" / "monitor-events-v1.json").read_text(encoding="utf-8")
)


class MonitorEventContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        Draft202012Validator.check_schema(SCHEMA)
        cls.validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

    def assertValid(self, event):
        errors = sorted(self.validator.iter_errors(event), key=lambda error: list(error.path))
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_frozen_v1_examples_remain_compatible(self):
        for event in COMPATIBILITY_EVENTS:
            with self.subTest(event=event["event"], detail=event.get("reason")):
                self.assertValid(event)

    def test_current_emitter_matches_v1_schema_for_every_event_kind(self):
        events = []
        config = MonitorConfig(
            "sqlite://", "redis://synthetic", interval_seconds=1,
            max_consecutive_errors=2, max_samples=2,
        )

        result = run_monitor(
            Mock(side_effect=[health(), RuntimeError("synthetic outage")]),
            Event(), config, emit=events.append,
            clock=lambda: datetime(2026, 11, 17, tzinfo=timezone.utc),
            wait=lambda _seconds: False,
        )

        self.assertEqual(result, 0)
        self.assertEqual([event["event"] for event in events],
                         ["sample", "sample_error", "stopped"])
        self.assertEqual(MONITOR_EVENT_SCHEMA_VERSION, 1)
        for event in events:
            self.assertValid(json.loads(json.dumps(event)))

    def test_unknown_or_missing_versions_and_extra_fields_are_rejected(self):
        original = COMPATIBILITY_EVENTS[-1]
        for changed in (
            {key: value for key, value in original.items() if key != "schema_version"},
            {**original, "schema_version": 2},
            {**original, "workflow_id": "synthetic-payload-identifier"},
        ):
            with self.subTest(changed=changed):
                self.assertTrue(list(self.validator.iter_errors(changed)))
