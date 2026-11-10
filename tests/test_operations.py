import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from uuid import uuid4

from fakeredis import FakeRedis
from sqlalchemy import create_engine

from flowledger.claims import CLAIM_KEYS, RedisClaimQueue
from flowledger.database import Base, OutboxRecord
from flowledger.domain import Workflow
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand
from flowledger.operations import health_snapshot, outbox_health
from flowledger.outbox import relay_pending
from flowledger.repository import SqlAlchemyRepository, build_session_factory


class OperationalHealthTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'health.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = build_session_factory(self.engine)
        self.repository = SqlAlchemyRepository(self.sessions)
        self.redis = FakeRedis()
        self.queue = RedisClaimQueue(self.redis)
        self.submitter = RedisSubmissionQueue(self.redis)

    def stage(self):
        workflow = Workflow.create("synthetic operational health")
        command = WorkerCommand(command_id=uuid4(), workflow_id=workflow.id,
                                expected_version=0)
        self.repository.add(workflow, command)
        return command

    def test_empty_snapshot_has_no_invented_backlog_age(self):
        snapshot = health_snapshot(self.sessions, self.queue)

        self.assertEqual((snapshot.outbox.pending, snapshot.outbox.oldest_age_seconds),
                         (0, None))
        self.assertEqual(snapshot.redis.pending.value, 0)

    def test_outbox_age_uses_oldest_undelivered_row(self):
        command = self.stage()
        observed_at = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
        with self.sessions.begin() as session:
            session.get(OutboxRecord, command.command_id).created_at = (
                observed_at - timedelta(seconds=90)
            )

        result = outbox_health(self.sessions, now=observed_at)

        self.assertEqual(result.pending, 1)
        self.assertEqual(result.oldest_age_seconds, 90)

    def test_delivered_outbox_rows_leave_the_backlog(self):
        self.stage()
        relay_pending(self.sessions, self.submitter)

        self.assertEqual(outbox_health(self.sessions).pending, 0)
        self.assertEqual(self.redis.llen(CLAIM_KEYS[0]), 1)

    def test_redis_snapshot_is_atomic_payload_free_and_capped(self):
        self.redis.rpush(CLAIM_KEYS[0], "secret-one", "secret-two", "secret-three")
        self.redis.hset(CLAIM_KEYS[1], mapping={"token-a": "secret-four"})
        self.redis.zadd(CLAIM_KEYS[4], {"secret-five": 1})
        self.redis.rpush(CLAIM_KEYS[5], "secret-six")
        self.redis.rpush(CLAIM_KEYS[6], "secret-seven")

        result = self.queue.health_snapshot(count_limit=2)

        self.assertEqual((result.pending.value, result.pending.capped), (2, True))
        for name in ("claimed", "delayed", "quarantined", "exhausted"):
            self.assertEqual((getattr(result, name).value,
                              getattr(result, name).capped), (1, False))
        self.assertNotIn("secret", repr(result))

    def test_redis_type_errors_and_invalid_limits_are_not_hidden(self):
        self.redis.set(CLAIM_KEYS[4], "wrong-type")
        with self.assertRaisesRegex(Exception, "Invalid claim key type"):
            self.queue.health_snapshot()
        for value in (0, -1, True, 2_147_483_648):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.queue.health_snapshot(value)

    def test_naive_observation_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            outbox_health(self.sessions, now=datetime(2026, 1, 1))
