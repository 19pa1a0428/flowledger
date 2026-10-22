from threading import Event
from unittest.mock import ANY, Mock, patch
from uuid import uuid4
import unittest

from flowledger.claims import Claim
from flowledger.jobs import WorkerCommand
from flowledger.worker import WorkerLoopConfig, WorkerLoopStats, run_loop


class StopOnWait:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.stopped = True
        return True


class WorkerLoopTests(unittest.TestCase):
    def test_idle_cycle_runs_maintenance_then_waits(self):
        queue = Mock()
        queue.recover_expired.return_value = 2
        queue.promote_due.return_value = 1
        stop = StopOnWait()
        config = WorkerLoopConfig(maintenance_batch_size=7, idle_wait_seconds=0.25)

        with patch("flowledger.worker.run_once", return_value=False) as poll:
            result = run_loop(queue, Mock(), stop, config)

        self.assertEqual(result, WorkerLoopStats(1, 0, 2, 1))
        queue.recover_expired.assert_called_once_with(limit=7)
        queue.promote_due.assert_called_once_with(limit=7)
        poll.assert_called_once_with(queue, ANY, lease_ms=30_000)
        self.assertEqual(stop.waits, [0.25])

    def test_full_batch_yields_to_maintenance_without_idle_wait(self):
        queue = Mock()
        queue.recover_expired.return_value = 0
        queue.promote_due.return_value = 0
        stop = StopOnWait()
        config = WorkerLoopConfig(work_batch_size=2)

        with patch("flowledger.worker.run_once", side_effect=[True, True, False]) as poll:
            result = run_loop(queue, Mock(), stop, config)

        self.assertEqual(result, WorkerLoopStats(2, 2, 0, 0))
        self.assertEqual(queue.recover_expired.call_count, 2)
        self.assertEqual(queue.promote_due.call_count, 2)
        self.assertEqual(poll.call_count, 3)
        self.assertEqual(stop.waits, [1.0])

    def test_shutdown_during_execution_acknowledges_current_claim_only(self):
        stop = Event()
        first = Claim("first", WorkerCommand(command_id=uuid4(), workflow_id=uuid4(),
                                              expected_version=0))
        second = Claim("second", WorkerCommand(command_id=uuid4(), workflow_id=uuid4(),
                                                expected_version=0))
        queue = Mock()
        queue.claim.side_effect = [first, second]
        queue.recover_expired.return_value = 0
        queue.promote_due.return_value = 0
        repository = Mock()
        repository.execute.side_effect = lambda *_args, **_kwargs: stop.set()

        result = run_loop(queue, repository, stop, WorkerLoopConfig(lease_ms=1234))

        self.assertEqual(result, WorkerLoopStats(1, 1, 0, 0))
        self.assertTrue(stop.is_set())
        queue.claim.assert_called_once_with(lease_ms=1234)
        queue.acknowledge.assert_called_once_with("first")

    def test_loop_configuration_rejects_unbounded_or_invalid_values(self):
        cases = [
            {"work_batch_size": 0}, {"maintenance_batch_size": 1001},
            {"work_batch_size": True}, {"lease_ms": 0},
            {"idle_wait_seconds": 0}, {"idle_wait_seconds": float("inf")},
            {"idle_wait_seconds": True},
        ]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    WorkerLoopConfig(**values)


if __name__ == "__main__":
    unittest.main()
