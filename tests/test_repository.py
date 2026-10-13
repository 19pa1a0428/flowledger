import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from flowledger.database import Base
from flowledger.domain import Status, Workflow
from flowledger.repository import ConcurrencyConflict, SqlAlchemyRepository, build_session_factory


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database = Path(self.temp_dir.name) / "workflows.db"
        self.engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(self.engine)
        self.repository = SqlAlchemyRepository(build_session_factory(self.engine))

    def test_data_survives_new_repository_and_engine(self):
        workflow = self.repository.add(Workflow.create("durable synthetic job"))
        self.repository.transition(workflow.id, Status.RUNNING, workflow.version)
        self.engine.dispose()
        restarted_engine = create_engine(str(self.engine.url))
        self.addCleanup(restarted_engine.dispose)
        restarted = SqlAlchemyRepository(build_session_factory(restarted_engine))
        restored = restarted.get(workflow.id)
        self.assertEqual(restored.status, Status.RUNNING)
        self.assertEqual(restored.attempts, 1)
        self.assertEqual([event.status for event in restored.events],
                         [Status.PENDING, Status.RUNNING])
        self.assertTrue(all(event.occurred_at.tzinfo is not None for event in restored.events))

    def test_failed_commit_rolls_back_snapshot_and_event(self):
        workflow = self.repository.add(Workflow.create("atomic synthetic job"))

        class FailingCommitSession(Session):
            pass

        failing_sessions = sessionmaker(
            self.engine, class_=FailingCommitSession, expire_on_commit=False
        )

        def reject_commit(session):
            raise RuntimeError("synthetic commit failure")

        event.listen(FailingCommitSession, "before_commit", reject_commit, once=True)
        failing = SqlAlchemyRepository(failing_sessions)
        with self.assertRaisesRegex(RuntimeError, "synthetic commit failure"):
            failing.transition(workflow.id, Status.RUNNING, workflow.version)
        restored = self.repository.get(workflow.id)
        self.assertEqual(restored.status, Status.PENDING)
        self.assertEqual(restored.attempts, 0)
        self.assertEqual(len(restored.events), 1)

    def test_stale_writer_cannot_overwrite_snapshot_or_append_event(self):
        workflow = self.repository.add(Workflow.create("concurrent synthetic job"))
        writer_one = SqlAlchemyRepository(build_session_factory(self.engine))
        writer_two = SqlAlchemyRepository(build_session_factory(self.engine))

        updated = writer_one.transition(
            workflow.id, Status.RUNNING, expected_version=workflow.version
        )
        with self.assertRaisesRegex(ConcurrencyConflict, "Expected version 0, found 1"):
            writer_two.transition(
                workflow.id, Status.CANCELLED, expected_version=workflow.version
            )

        restored = self.repository.get(workflow.id)
        self.assertEqual(restored, updated)
        self.assertEqual([event.status for event in restored.events],
                         [Status.PENDING, Status.RUNNING])
