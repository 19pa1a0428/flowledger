import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fakeredis import FakeRedis
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session, sessionmaker

from flowledger.claims import RedisClaimQueue
from flowledger.database import Base, CommandReceipt, OutboxRecord, WorkflowRecord, EventRecord
from flowledger.domain import Workflow
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand, SubmissionConflict
from flowledger.metrics import FlowLedgerMetrics
from flowledger.outbox import reconcile_delivered, relay_pending
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from flowledger.worker import run_once


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = f"sqlite:///{Path(self.temp.name) / 'outbox.db'}"
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = build_session_factory(self.engine)
        self.repo = SqlAlchemyRepository(self.sessions)
        self.redis = FakeRedis()
        self.queue = RedisSubmissionQueue(self.redis)

    def stage(self):
        workflow = Workflow.create('synthetic outbox job')
        command = WorkerCommand(command_id=uuid4(), workflow_id=workflow.id, expected_version=0)
        self.repo.add(workflow, command)
        return command

    def test_durable_intent_relay_and_bounded_batch(self):
        self.stage()
        self.stage()
        self.engine.dispose()
        restarted = create_engine(self.url)
        self.addCleanup(restarted.dispose)
        sessions = build_session_factory(restarted)
        self.assertEqual(relay_pending(sessions, self.queue, limit=1), 1)
        self.assertEqual(relay_pending(sessions, self.queue), 1)
        self.assertEqual(relay_pending(sessions, self.queue), 0)
        self.assertEqual(self.redis.llen('flowledger:{jobs}:pending'), 2)

    def test_queue_failure_keeps_intent_pending(self):
        self.stage()
        broken = Mock()
        broken.submit.side_effect = ConnectionError('synthetic outage')
        with self.assertRaises(ConnectionError):
            relay_pending(self.sessions, broken)
        self.assertEqual(relay_pending(self.sessions, self.queue), 1)

    def test_metric_failure_does_not_change_relay_delivery(self):
        command = self.stage()
        meter = Mock()
        broken_counter = Mock()
        broken_counter.add.side_effect = RuntimeError("synthetic telemetry failure")
        meter.create_counter.return_value = broken_counter
        meter.create_gauge.return_value = Mock()
        metrics = FlowLedgerMetrics(meter, enabled=True)

        with patch("flowledger.metrics.LOGGER.exception") as logged:
            self.assertEqual(relay_pending(self.sessions, self.queue, metric_sink=metrics), 1)
        logged.assert_called_once()
        self.assertEqual(self.redis.llen("flowledger:{jobs}:pending"), 1)
        with self.sessions() as session:
            self.assertIsNotNone(session.get(OutboxRecord, command.command_id).delivered_at)

    def test_crash_after_redis_acceptance_replays_without_second_enqueue(self):
        self.stage()
        queue = self.queue

        class CrashAfterSubmit:
            def submit(self, command):
                queue.submit(command)
                raise RuntimeError('synthetic crash before DB delivery mark')

        with self.assertRaises(RuntimeError):
            relay_pending(self.sessions, CrashAfterSubmit())
        self.assertEqual(relay_pending(self.sessions, self.queue), 1)
        self.assertEqual(self.redis.llen('flowledger:{jobs}:pending'), 1)

    def test_commit_failure_rolls_back_workflow_event_and_intent(self):
        class BrokenSession(Session):
            pass

        def reject(session):
            session.flush()
            raise RuntimeError('synthetic commit failure')

        event.listen(BrokenSession, 'before_commit', reject, once=True)
        self.repo = SqlAlchemyRepository(sessionmaker(self.engine, class_=BrokenSession))
        with self.assertRaises(RuntimeError):
            self.stage()
        with self.sessions() as session:
            for model in (WorkflowRecord, EventRecord, OutboxRecord):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 0)

    def test_conflicting_receipt_does_not_mark_delivered(self):
        command = self.stage()
        self.queue.submit(command.model_copy(update={'expected_version': 1}))
        with self.assertRaises(SubmissionConflict):
            relay_pending(self.sessions, self.queue)
        with self.sessions() as session:
            self.assertIsNone(session.get(OutboxRecord, command.command_id).delivered_at)

    def test_invalid_intent_writes_nothing(self):
        workflow = Workflow.create('synthetic invalid intent')
        command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)
        with self.assertRaises(ValueError):
            self.repo.add(workflow, command)
        with self.assertRaises(KeyError):
            self.repo.get(workflow.id)

    def test_invalid_batch_bounds(self):
        for limit in (0, 1001, True, 1.5):
            with self.assertRaises(ValueError):
                relay_pending(self.sessions, self.queue, limit=limit)

    def lose_submission_state(self, command):
        self.assertEqual(relay_pending(self.sessions, self.queue), 1)
        self.redis.delete("flowledger:{jobs}:receipts", "flowledger:{jobs}:pending")
        with self.sessions() as session:
            self.assertIsNotNone(session.get(OutboxRecord, command.command_id).delivered_at)

    def test_reconciliation_dry_run_is_read_only(self):
        command = self.stage()
        self.lose_submission_state(command)

        report = reconcile_delivered(self.sessions, self.queue)

        self.assertEqual(report.missing_ids, (command.command_id,))
        self.assertEqual(report.requeued_ids, ())
        self.assertEqual(self.redis.llen("flowledger:{jobs}:pending"), 0)
        self.assertIsNone(self.queue.receipt_payload(command.command_id))

    def test_reconciliation_requeues_original_command_once(self):
        command = self.stage()
        self.lose_submission_state(command)

        first = reconcile_delivered(self.sessions, self.queue, requeue_missing=True)
        second = reconcile_delivered(self.sessions, self.queue, requeue_missing=True)

        self.assertEqual(first.requeued_ids, (command.command_id,))
        self.assertEqual((second.receipts_present, second.requeued_ids), (1, ()))
        self.assertEqual(self.redis.llen("flowledger:{jobs}:pending"), 1)
        self.assertEqual(self.queue.receipt_payload(command.command_id),
                         command.model_dump_json())

    def test_database_execution_receipt_prevents_needless_requeue(self):
        command = self.stage()
        self.assertEqual(relay_pending(self.sessions, self.queue), 1)
        self.assertTrue(run_once(RedisClaimQueue(self.redis), self.repo))
        self.redis.delete("flowledger:{jobs}:receipts")

        report = reconcile_delivered(self.sessions, self.queue, requeue_missing=True)

        self.assertEqual(report.scanned, 0)
        self.assertEqual(self.redis.llen("flowledger:{jobs}:pending"), 0)
        with self.sessions() as session:
            self.assertIsNotNone(session.get(CommandReceipt, command.command_id))

    def test_reconciliation_reports_changed_receipt_without_overwrite(self):
        command = self.stage()
        self.assertEqual(relay_pending(self.sessions, self.queue), 1)
        self.redis.hset("flowledger:{jobs}:receipts", str(command.command_id), "changed")

        report = reconcile_delivered(self.sessions, self.queue, requeue_missing=True)

        self.assertEqual(report.conflict_ids, (command.command_id,))
        self.assertEqual(report.requeued_ids, ())
        self.assertEqual(self.redis.hget("flowledger:{jobs}:receipts", str(command.command_id)),
                         b"changed")

    def test_reconciliation_outage_propagates_without_database_change(self):
        command = self.stage()
        self.lose_submission_state(command)
        broken = Mock()
        broken.receipt_payload.return_value = None
        broken.submit.side_effect = ConnectionError("synthetic reconciliation outage")

        with self.assertRaises(ConnectionError):
            reconcile_delivered(self.sessions, broken, requeue_missing=True)

        with self.sessions() as session:
            row = session.get(OutboxRecord, command.command_id)
            self.assertIsNotNone(row.delivered_at)
            self.assertIsNone(session.get(CommandReceipt, command.command_id))

    def test_concurrent_reconciliation_converges_on_identical_receipt(self):
        command = self.stage()
        self.lose_submission_state(command)
        racing = Mock()
        racing.receipt_payload.return_value = None
        racing.submit.return_value = False

        report = reconcile_delivered(self.sessions, racing, requeue_missing=True)

        self.assertEqual(report.missing_ids, (command.command_id,))
        self.assertEqual(report.converged_ids, (command.command_id,))
        self.assertEqual(report.requeued_ids, ())

    def test_reconciliation_validates_bounds_and_mode(self):
        for limit in (0, 1001, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    reconcile_delivered(self.sessions, self.queue, limit=limit)
        with self.assertRaises(TypeError):
            reconcile_delivered(self.sessions, self.queue, requeue_missing=1)
