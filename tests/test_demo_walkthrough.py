from unittest import TestCase

from scripts.demo_walkthrough import run_demo


class ScriptedApi:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, path, *, method="GET", body=None):
        self.calls.append((method, path, body))
        return next(self.responses)


def workflow(status, version, attempts, events=None):
    return {
        "id": "00000000-0000-0000-0000-000000000025",
        "status": status,
        "version": version,
        "attempts": attempts,
        "events": events or [],
    }


class DemoWalkthroughTests(TestCase):
    def test_walkthrough_proves_delivery_conflict_retry_and_completion(self):
        history = [
            {"status": status} for status in
            ("pending", "running", "failed", "pending", "running", "succeeded")
        ]
        api = ScriptedApi([
            (200, {"status": "ok"}),
            (201, workflow("pending", 0, 0)),
            (200, workflow("pending", 0, 0)),
            (200, workflow("running", 1, 1)),
            (200, workflow("failed", 2, 1)),
            (409, {"detail": "Workflow version changed"}),
            (200, workflow("pending", 3, 1)),
            (200, workflow("running", 4, 2)),
            (200, workflow("succeeded", 5, 2)),
            (200, workflow("succeeded", 5, 2, history)),
        ])

        result = run_demo(api, clock=lambda: 0.0, sleep=lambda _: None)

        self.assertEqual(result.final_status, "succeeded")
        self.assertEqual(result.attempts, 2)
        stale_call = api.calls[5]
        self.assertEqual(stale_call[2], {"status": "succeeded", "expected_version": 1})
        self.assertEqual(
            result.event_statuses,
            ("pending", "running", "failed", "pending", "running", "succeeded"),
        )

    def test_walkthrough_stops_when_readiness_fails(self):
        api = ScriptedApi([(503, {"status": "unavailable"})])

        with self.assertRaisesRegex(RuntimeError, "readiness check returned HTTP 503"):
            run_demo(api)

        self.assertEqual(len(api.calls), 1)

    def test_walkthrough_times_out_before_issuing_transitions(self):
        ticks = iter((0.0, 1.0))
        api = ScriptedApi([
            (200, {"status": "ok"}),
            (201, workflow("pending", 0, 0)),
        ])

        with self.assertRaisesRegex(RuntimeError, "worker did not move"):
            run_demo(api, timeout=1.0, clock=lambda: next(ticks), sleep=lambda _: None)

        self.assertEqual([call[1] for call in api.calls], ["/health/ready", "/workflows"])

    def test_walkthrough_rejects_an_incomplete_final_history(self):
        api = ScriptedApi([
            (200, {"status": "ok"}),
            (201, workflow("running", 1, 1)),
            (200, workflow("failed", 2, 1)),
            (409, {"detail": "Workflow version changed"}),
            (200, workflow("pending", 3, 1)),
            (200, workflow("running", 4, 2)),
            (200, workflow("succeeded", 5, 2)),
            (200, workflow("succeeded", 5, 2, [{"status": "succeeded"}])),
        ])

        with self.assertRaisesRegex(RuntimeError, "expected retry history"):
            run_demo(api)
