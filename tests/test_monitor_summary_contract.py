from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from jsonschema import Draft202012Validator, FormatChecker

from flowledger.monitor_log import SUMMARY_SCHEMA_VERSION, inspect_log


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "monitor-summary-v1.schema.json").read_text(encoding="utf-8")
)
COMPATIBILITY_SUMMARY = json.loads(
    (ROOT / "tests" / "fixtures" / "monitor-summary-v1.json").read_text(
        encoding="utf-8"
    )
)


class MonitorSummaryContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        Draft202012Validator.check_schema(SCHEMA)
        cls.validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

    def assertValid(self, summary):
        errors = sorted(
            self.validator.iter_errors(summary), key=lambda error: list(error.path)
        )
        self.assertEqual(errors, [], [error.message for error in errors])

    def test_frozen_v1_example_remains_compatible(self):
        self.assertValid(COMPATIBILITY_SUMMARY)

    def test_current_inspector_matches_v1_schema(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.jsonl"
            path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "event": "stopped",
                    "attempts": 0,
                    "errors": 0,
                    "samples": 0,
                    "reason": "signal",
                }) + "\n",
                encoding="utf-8",
            )
            summary = inspect_log(path)

        self.assertEqual(summary["summary_version"], SUMMARY_SCHEMA_VERSION)
        self.assertValid(summary)

    def test_missing_unknown_versions_and_extra_fields_are_rejected(self):
        without_version = deepcopy(COMPATIBILITY_SUMMARY)
        del without_version["summary_version"]
        for changed in (
            without_version,
            {**COMPATIBILITY_SUMMARY, "summary_version": 2},
            {**COMPATIBILITY_SUMMARY, "workflow_id": "synthetic-identifier"},
        ):
            with self.subTest(changed=changed):
                self.assertTrue(list(self.validator.iter_errors(changed)))
