import subprocess
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts.telemetry_smoke import (
    CORRELATION_EVIDENCE, EVIDENCE, collector_logs, missing_evidence, smoke,
)


COMPLETE_LOGS = """
otel-collector-1  | Resource attributes: service.name: Str(flowledger-api)
otel-collector-1  | Span #0
otel-collector-1  |     Trace ID       : aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
otel-collector-1  |     Name           : HTTP POST /workflows
otel-collector-1  | Resource attributes: service.name: Str(flowledger-worker)
otel-collector-1  | Span #1
otel-collector-1  |     Trace ID       : aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
otel-collector-1  |     Name           : worker.execute
otel-collector-1  | flowledger.relay.commands
otel-collector-1  | flowledger.worker.commands
otel-collector-1  | flowledger.queue.depth
"""


class TelemetrySmokeTests(TestCase):
    def test_missing_evidence_reports_only_fixed_signal_names(self):
        self.assertEqual(missing_evidence("flowledger-api worker.execute"), (
            "HTTP POST /workflows", "flowledger-worker",
            "flowledger.relay.commands", "flowledger.worker.commands",
            "flowledger.queue.depth", CORRELATION_EVIDENCE,
        ))
        self.assertEqual(missing_evidence(" ".join(EVIDENCE)),
                         (CORRELATION_EVIDENCE,))
        self.assertEqual(missing_evidence(COMPLETE_LOGS), ())

    def test_smoke_waits_for_worker_and_complete_collector_evidence(self):
        request = Mock(side_effect=[
            (201, {"id": "00000000-0000-0000-0000-000000000001"}),
            (200, {"status": "pending"}),
            (200, {"status": "running"}),
        ])
        logs = Mock(side_effect=["flowledger-api", COMPLETE_LOGS])
        pause = Mock()

        smoke(request_fn=request, logs_fn=logs, attempts=2, pause=pause)

        self.assertEqual(request.call_count, 3)
        self.assertEqual(logs.call_count, 2)
        self.assertEqual(pause.call_count, 2)

    def test_smoke_fails_when_worker_or_signals_never_arrive(self):
        create = (201, {"id": "00000000-0000-0000-0000-000000000001"})
        with self.assertRaisesRegex(RuntimeError, "worker did not process"):
            smoke(request_fn=Mock(side_effect=[create, (200, {"status": "pending"})]),
                  attempts=1, pause=Mock())
        with self.assertRaisesRegex(RuntimeError, "Collector evidence missing"):
            smoke(request_fn=Mock(side_effect=[create, (200, {"status": "running"})]),
                  logs_fn=Mock(return_value=""), attempts=1, pause=Mock())

    @patch("scripts.telemetry_smoke.subprocess.run")
    def test_collector_logs_use_optional_profile_without_mutation(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "out", "err")

        self.assertEqual(collector_logs(), "outerr")

        command = run.call_args.args[0]
        self.assertIn("compose.otel.yaml", command)
        self.assertIn("telemetry", command)
        self.assertEqual(command[-3:], ("logs", "--no-color", "otel-collector"))
