import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fakeredis import FakeRedis
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session, sessionmaker

from flowledger.database import Base, OutboxRecord, WorkflowRecord, EventRecord
from flowledger.domain import Workflow
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand, SubmissionConflict
from flowledger.outbox import relay_pending
from flowledger.repository import SqlAlchemyRepository, build_session_factory


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
