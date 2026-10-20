from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
import unittest

from sqlalchemy import event, select, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError, IntegrityError

from flowledger.claims import RedisClaimQueue
from flowledger.database import CommandReceipt
from flowledger.domain import Status
from flowledger.jobs import SubmissionConflict, WorkerCommand
from flowledger.repository import SqlAlchemyRepository, ConcurrencyConflict
from flowledger.worker import run_once
from tests import test_outbox


class WorkerTests(unittest.TestCase):
    setUp = test_outbox.OutboxTests.setUp
    stage = test_outbox.OutboxTests.stage
    def test_replay_after_restart_and_payload_conflict(self):
        command = self.stage()
        self.assertTrue(self.repo.execute(command))
        self.engine.dispose()
        restarted = SqlAlchemyRepository(self.sessions)
        self.assertFalse(restarted.execute(command))
        with self.assertRaises(SubmissionConflict):
            restarted.execute(command.model_copy(update={"target": Status.CANCELLED}))
        workflow = restarted.get(command.workflow_id)
        self.assertEqual((workflow.version, len(workflow.events)), (1, 2))

    def test_crash_after_commit_before_ack_is_safe(self):
        command = self.stage()
        self.queue.submit(command)
        claims = RedisClaimQueue(self.redis)
        with patch.object(claims, "acknowledge", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                run_once(claims, self.repo)
        token = self.redis.zrange("flowledger:{jobs}:deadlines", 0, -1)[0]
        self.redis.zadd("flowledger:{jobs}:deadlines", {token: 0})
        self.assertEqual(claims.recover_expired(), 1)
        self.redis.zadd("flowledger:{jobs}:delayed", {command.model_dump_json(): 0})
        claims.promote_due()
        self.assertTrue(run_once(claims, self.repo))
        self.assertFalse(run_once(claims, self.repo))
        self.assertEqual(len(self.repo.get(command.workflow_id).events), 2)

    def test_transaction_failure_rolls_back_receipt_and_effect(self):
        command = self.stage()
        class Broken(Session):
            pass
        def reject(session):
            session.flush()
            raise RuntimeError("synthetic crash")
        event.listen(Broken, "before_commit", reject, once=True)
        broken = SqlAlchemyRepository(sessionmaker(self.engine, class_=Broken))
        with self.assertRaises(RuntimeError):
            broken.execute(command)
        self.assertEqual(self.repo.get(command.workflow_id).version, 0)
        with self.sessions() as session:
            self.assertIsNone(session.get(CommandReceipt, command.command_id))
        self.assertTrue(self.repo.execute(command))

    def test_stale_command_does_not_leave_receipt(self):
        command = self.stage()
        self.repo.transition(command.workflow_id, Status.RUNNING, 0)
        with self.assertRaises(ConcurrencyConflict):
            self.repo.execute(command)
        with self.sessions() as session:
            self.assertIsNone(session.get(CommandReceipt, command.command_id))

    def test_concurrent_duplicates_apply_once(self):
        command = self.stage()
        def execute(_):
            # Uniqueness races may reject a writer; retry in a fresh transaction.
            from sqlalchemy.exc import IntegrityError, OperationalError
            try:
                return self.repo.execute(command)
            except (IntegrityError, OperationalError):
                return self.repo.execute(command)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(execute, range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.repo.get(command.workflow_id).events), 2)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CommandReceipt)), 1)

    def test_worker_retains_terminal_rejections_without_workflow_effects(self):
        command = self.stage()
        self.repo.transition(command.workflow_id, Status.RUNNING, 0)
        cases = [
            (command, "stale_version"),
            (WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0),
             "workflow_missing"),
            (command.model_copy(update={"command_id": uuid4(), "expected_version": 1,
                                        "target": Status.PENDING}), "invalid_transition"),
        ]
        claims = RedisClaimQueue(self.redis)
        for rejected, reason in cases:
            with self.subTest(reason=reason):
                self.queue.submit(rejected)
                self.assertTrue(run_once(claims, self.repo))
                self.assertFalse(run_once(claims, self.repo))
                with self.sessions() as session:
                    receipt = session.get(CommandReceipt, rejected.command_id)
                    self.assertEqual(receipt.rejection, reason)
                    self.assertEqual(receipt.payload, rejected.model_dump_json())
        self.assertEqual(len(self.repo.get(command.workflow_id).events), 2)
        self.assertEqual(self.redis.hlen("flowledger:{jobs}:claims"), 0)

    def test_rejected_receipt_survives_restart_and_crash_before_ack(self):
        command = self.stage()
        self.repo.transition(command.workflow_id, Status.RUNNING, 0)
        self.queue.submit(command)
        claims = RedisClaimQueue(self.redis)
        with patch.object(claims, "acknowledge", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                run_once(claims, self.repo)
        self.engine.dispose()
        restarted = SqlAlchemyRepository(self.sessions)
        token = self.redis.zrange("flowledger:{jobs}:deadlines", 0, -1)[0]
        self.redis.zadd("flowledger:{jobs}:deadlines", {token: 0})
        claims.recover_expired()
        self.redis.zadd("flowledger:{jobs}:delayed", {command.model_dump_json(): 0})
        claims.promote_due()
        with patch.object(restarted, "_transition", side_effect=AssertionError("reapplied")):
            self.assertTrue(run_once(claims, restarted))
        with self.assertRaises(SubmissionConflict):
            restarted.execute(command.model_copy(update={"expected_version": 1}),
                              reject_invalid=True)
        self.assertEqual(len(restarted.get(command.workflow_id).events), 2)

    def test_rejection_commit_failure_is_not_acknowledged(self):
        command = self.stage()
        self.repo.transition(command.workflow_id, Status.RUNNING, 0)
        self.queue.submit(command)
        class Broken(Session):
            pass
        def reject(session):
            # Nested savepoint commit is not the durable outer commit.
            if not session.in_nested_transaction():
                session.flush()
                raise RuntimeError("synthetic commit outage")
        event.listen(Broken, "before_commit", reject)
        broken = SqlAlchemyRepository(sessionmaker(self.engine, class_=Broken))
        claims = RedisClaimQueue(self.redis)
        with patch.object(claims, "acknowledge") as ack:
            with self.assertRaises(RuntimeError):
                run_once(claims, broken)
            ack.assert_not_called()
        with self.sessions() as session:
            self.assertIsNone(session.get(CommandReceipt, command.command_id))
        self.assertEqual(self.redis.hlen("flowledger:{jobs}:claims"), 1)

    def test_unexpected_error_is_not_terminal(self):
        command = self.stage()
        self.queue.submit(command)
        claims = RedisClaimQueue(self.redis)
        with patch.object(self.repo, "_transition", side_effect=RuntimeError("outage")):
            with self.assertRaises(RuntimeError):
                run_once(claims, self.repo)
        with self.sessions() as session:
            self.assertIsNone(session.get(CommandReceipt, command.command_id))
        self.assertEqual(self.repo.get(command.workflow_id).version, 0)
        self.assertEqual(self.redis.hlen("flowledger:{jobs}:claims"), 1)

    def test_database_errors_schedule_retry_then_apply_once(self):
        for error_type in (OperationalError, IntegrityError):
            with self.subTest(error=error_type.__name__):
                command = self.stage()
                self.queue.submit(command)
                claims = RedisClaimQueue(self.redis)
                error = error_type("synthetic", {}, RuntimeError("synthetic outage"))
                with patch.object(self.repo, "execute", side_effect=error):
                    self.assertTrue(run_once(claims, self.repo))
                with self.sessions() as session:
                    self.assertIsNone(session.get(CommandReceipt, command.command_id))
                self.assertEqual(self.repo.get(command.workflow_id).version, 0)
                self.assertEqual(self.redis.hlen("flowledger:{jobs}:claims"), 0)
                self.assertFalse(run_once(claims, self.repo))
                self.redis.zadd("flowledger:{jobs}:delayed", {command.model_dump_json(): 0})
                claims.promote_due()
                self.assertTrue(run_once(claims, self.repo))
                self.assertEqual(self.repo.get(command.workflow_id).version, 1)
                self.assertEqual(self.redis.hlen("flowledger:{jobs}:failures"), 0)

    def test_retry_scheduling_outage_leaves_claim_recoverable(self):
        command = self.stage()
        self.queue.submit(command)
        claims = RedisClaimQueue(self.redis)
        error = OperationalError("synthetic", {}, RuntimeError("synthetic outage"))
        with patch.object(self.repo, "execute", side_effect=error):
            with patch.object(claims, "retry", side_effect=ConnectionError):
                with self.assertRaises(ConnectionError):
                    run_once(claims, self.repo)
        self.assertEqual(self.redis.hlen("flowledger:{jobs}:claims"), 1)
        with self.sessions() as session:
            self.assertIsNone(session.get(CommandReceipt, command.command_id))

    def test_ambiguous_commit_retries_without_duplicating_effect(self):
        command = self.stage()
        self.queue.submit(command)
        claims = RedisClaimQueue(self.redis)
        def commit_then_disconnect(*args, **kwargs):
            self.repo.execute(*args, **kwargs)
            raise OperationalError("synthetic", {}, RuntimeError("lost commit response"))
        from unittest.mock import Mock
        ambiguous = Mock()
        ambiguous.execute.side_effect = commit_then_disconnect
        self.assertTrue(run_once(claims, ambiguous))
        self.assertEqual(self.repo.get(command.workflow_id).version, 1)
        self.redis.zadd("flowledger:{jobs}:delayed", {command.model_dump_json(): 0})
        claims.promote_due()
        self.assertTrue(run_once(claims, self.repo))
        self.assertEqual(len(self.repo.get(command.workflow_id).events), 2)
