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
        workflow = self.repository.add(Workflow.create(
            "durable synthetic job", owner_id="synthetic-owner"
        ))
        self.repository.transition(workflow.id, Status.RUNNING, workflow.version,
                                   actor_id="synthetic-worker")
        self.engine.dispose()
        restarted_engine = create_engine(str(self.engine.url))
        self.addCleanup(restarted_engine.dispose)
        restarted = SqlAlchemyRepository(build_session_factory(restarted_engine))
        restored = restarted.get(workflow.id)
        self.assertEqual(restored.status, Status.RUNNING)
        self.assertEqual(restored.owner_id, "synthetic-owner")
        self.assertEqual(restored.attempts, 1)
        self.assertEqual([event.status for event in restored.events],
                         [Status.PENDING, Status.RUNNING])
        self.assertEqual([event.actor_id for event in restored.events],
                         ["synthetic-owner", "synthetic-worker"])
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

    def test_audit_query_filters_actor_and_status(self):
        first = self.repository.add(Workflow.create(
            "first audit job", owner_id="owner-one", actor_id="actor-one"
        ))
        self.repository.transition(first.id, Status.RUNNING, first.version,
                                   actor_id="actor-two")
        second = self.repository.add(Workflow.create(
            "second audit job", owner_id="owner-two", actor_id="actor-one"
        ))

        page = self.repository.list_audit_events(
            actor_id="actor-one", status=Status.PENDING,
        )
        self.assertEqual([item.workflow_id for item in page.items],
                         [second.id, first.id])
        self.assertEqual([item.owner_id for item in page.items],
                         ["owner-two", "owner-one"])
        self.assertTrue(all(item.actor_id == "actor-one" for item in page.items))
        self.assertTrue(all(item.status is Status.PENDING for item in page.items))
        self.assertTrue(all(item.occurred_at.tzinfo is not None for item in page.items))
        self.assertIsNone(page.next_cursor)

    def test_audit_cursor_is_stable_when_new_events_arrive(self):
        first = self.repository.add(Workflow.create(
            "first page job", owner_id="owner-one"
        ))
        self.repository.transition(first.id, Status.RUNNING, first.version,
                                   actor_id="operator")
        self.repository.add(Workflow.create("second page job", owner_id="owner-two"))

        first_page = self.repository.list_audit_events(limit=2)
        self.assertEqual(len(first_page.items), 2)
        self.assertIsNotNone(first_page.next_cursor)
        self.repository.add(Workflow.create("later job", owner_id="owner-three"))
        second_page = self.repository.list_audit_events(
            limit=2, cursor=first_page.next_cursor,
        )

        first_ids = {item.event_id for item in first_page.items}
        self.assertTrue(first_ids.isdisjoint(item.event_id for item in second_page.items))
        self.assertEqual([item.workflow_id for item in second_page.items], [first.id])
        self.assertIsNone(second_page.next_cursor)
