from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from flowledger.monitor_log import (
    MAX_INPUT_BYTES,
    MAX_RECORDS,
    MonitorLogError,
    inspect_log,
    load_validators,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
EVENTS = json.loads(
    (ROOT / "tests" / "fixtures" / "monitor-events-v1.json").read_text(encoding="utf-8")
)


class MonitorLogTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "monitor.jsonl"

    def write_events(self, events):
        self.path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

    def test_summary_is_deterministic_and_payload_free(self):
        self.write_events(EVENTS)

        summary = inspect_log(self.path)

        self.assertEqual(summary, {
            "event_counts": {"sample": 3, "sample_error": 1, "stopped": 3},
            "latest_status": {
                "oldest_outbox_age": "clear",
                "terminal_queue_growth": "clear",
            },
            "observation_window": {
                "first": "2026-11-16T19:00:00-06:00",
                "last": "2026-11-16T19:03:00-06:00",
            },
            "records": 7,
            "schema_versions": [1],
            "stop_reasons": {
                "signal": 1,
                "max_samples": 1,
                "consecutive_sample_errors": 1,
            },
            "transitions": {
                "oldest_outbox_age": {"fired": 1, "recovered": 1},
                "terminal_queue_growth": {"fired": 1, "recovered": 1},
            },
        })
        self.assertNotIn("error_type", repr(summary))
        self.assertNotIn("outbox_pending", repr(summary))

    def test_rejects_unknown_versions_malformed_records_and_private_values(self):
        invalid = (
            [{**EVENTS[0], "schema_version": 2}],
            [{key: value for key, value in EVENTS[0].items()
              if key != "schema_version"}],
            [{**EVENTS[0], "workflow_id": "private-synthetic-value"}],
        )
        for events in invalid:
            with self.subTest(events=events):
                self.write_events(events)
                with self.assertRaises(MonitorLogError) as caught:
                    inspect_log(self.path)
                self.assertNotIn("private-synthetic-value", str(caught.exception))

        for content in ("not-json\n", "[]\n", "\n"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(MonitorLogError):
                    inspect_log(self.path)

    def test_input_bytes_and_record_count_are_bounded_before_validation(self):
        self.path.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
        with self.assertRaisesRegex(MonitorLogError, "bytes"):
            inspect_log(self.path)

        self.path.write_text("{}\n" * (MAX_RECORDS + 1), encoding="utf-8")
        with self.assertRaisesRegex(MonitorLogError, "records"):
            inspect_log(self.path)

    def test_cli_has_stable_status_and_does_not_echo_rejected_record(self):
        self.write_events(EVENTS)
        stdout = StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main([str(self.path)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["records"], 7)

        self.path.write_text(
            '{"schema_version":1,"private":"do-not-echo"}\n', encoding="utf-8"
        )
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(main([str(self.path)]), 2)
        self.assertIn("record 1", stderr.getvalue())
        self.assertNotIn("do-not-echo", stderr.getvalue())

    def test_schema_registry_and_packaged_copy_include_version_one(self):
        self.assertEqual(set(load_validators()), {1})
        published = (ROOT / "schemas" / "monitor-event-v1.schema.json").read_bytes()
        packaged = (
            ROOT / "flowledger" / "schemas" / "monitor-event-v1.schema.json"
        ).read_bytes()
        self.assertEqual(packaged, published)
