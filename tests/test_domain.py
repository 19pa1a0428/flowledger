import unittest
from dataclasses import FrozenInstanceError

from flowledger.domain import InvalidTransition, Status, Workflow


class WorkflowTests(unittest.TestCase):
    def test_success_keeps_original_snapshot_and_audit_history(self):
        pending = Workflow.create("  synthetic report  ", owner_id="synthetic-owner")
        running = pending.transition(Status.RUNNING, actor_id="synthetic-worker")
        done = running.transition(Status.SUCCEEDED, actor_id="synthetic-operator")
        self.assertEqual(pending.status, Status.PENDING)
        self.assertEqual(done.name, "synthetic report")
        self.assertEqual(done.owner_id, "synthetic-owner")
        self.assertEqual(done.attempts, 1)
        self.assertEqual((pending.version, running.version, done.version), (0, 1, 2))
        self.assertEqual([e.status for e in done.events],
                         [Status.PENDING, Status.RUNNING, Status.SUCCEEDED])
        self.assertEqual([e.actor_id for e in done.events],
                         ["synthetic-owner", "synthetic-worker", "synthetic-operator"])
        self.assertTrue(all(e.occurred_at.utcoffset().total_seconds() == 0
                            for e in done.events))
        with self.assertRaises(FrozenInstanceError):
            done.name = "changed"

    def test_every_status_pair_matches_transition_policy(self):
        pending = Workflow.create("example")
        running = pending.transition(Status.RUNNING)
        states = {
            Status.PENDING: pending,
            Status.RUNNING: running,
            Status.FAILED: running.transition(Status.FAILED),
            Status.SUCCEEDED: running.transition(Status.SUCCEEDED),
            Status.CANCELLED: pending.transition(Status.CANCELLED),
        }
        expected = {
            Status.PENDING: {Status.RUNNING, Status.CANCELLED},
            Status.RUNNING: {Status.SUCCEEDED, Status.FAILED},
            Status.FAILED: {Status.PENDING, Status.CANCELLED},
            Status.SUCCEEDED: set(), Status.CANCELLED: set(),
        }
        for source, workflow in states.items():
            for target in Status:
                with self.subTest(source=source, target=target):
                    if target in expected[source]:
                        self.assertEqual(workflow.transition(target).status, target)
                    else:
                        with self.assertRaises(InvalidTransition):
                            workflow.transition(target)

    def test_retry_budget_counts_total_execution_attempts(self):
        failed = Workflow.create("example", max_attempts=2)
        for attempt in (1, 2):
            failed = failed.transition(Status.RUNNING).transition(Status.FAILED)
            self.assertEqual(failed.attempts, attempt)
            if attempt < 2:
                failed = failed.transition(Status.PENDING)
        before = failed.events
        with self.assertRaises(InvalidTransition):
            failed.transition(Status.PENDING)
        self.assertEqual(failed.events, before)
        self.assertEqual(failed.transition(Status.CANCELLED).status, Status.CANCELLED)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            Workflow.create("  ")
        for value in (0, -1, True, 1.5, "3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Workflow.create("example", max_attempts=value)
        for value in ("", " user ", "x" * 101, None):
            with self.subTest(identity=value), self.assertRaises(ValueError):
                Workflow.create("example", owner_id=value)
        with self.assertRaises(ValueError):
            Workflow.create("example").transition(Status.RUNNING, actor_id=" ")

    def test_raw_string_status_is_rejected(self):
        with self.assertRaises(InvalidTransition):
            Workflow.create("example").transition("running")


if __name__ == "__main__":
    unittest.main()
