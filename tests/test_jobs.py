from concurrent.futures import ThreadPoolExecutor
import unittest
from uuid import uuid4

import fakeredis
from pydantic import ValidationError
from redis.exceptions import ConnectionError, ResponseError

from flowledger.jobs import RedisSubmissionQueue, SubmissionConflict, WorkerCommand


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()
        self.client = fakeredis.FakeRedis(server=self.server)
        self.queue = RedisSubmissionQueue(self.client)
        self.command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)

    def test_replay_after_new_client_does_not_enqueue_twice(self):
        self.assertTrue(self.queue.submit(self.command))
        other = RedisSubmissionQueue(fakeredis.FakeRedis(server=self.server))
        self.assertFalse(other.submit(self.command))
        values = self.client.lrange("flowledger:{jobs}:pending", 0, -1)
        self.assertEqual([WorkerCommand.model_validate_json(v) for v in values], [self.command])

    def test_changed_payload_conflicts(self):
        self.queue.submit(self.command)
        changed = self.command.model_copy(update={"expected_version": 1})
        with self.assertRaises(SubmissionConflict):
            self.queue.submit(changed)
        self.assertEqual(self.client.llen("flowledger:{jobs}:pending"), 1)

    def test_concurrent_duplicate_has_one_winner(self):
        def submit(_):
            return RedisSubmissionQueue(fakeredis.FakeRedis(server=self.server)).submit(self.command)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(24)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.client.llen("flowledger:{jobs}:pending"), 1)

    def test_distinct_commands_enqueue_in_order(self):
        other = self.command.model_copy(update={"command_id": uuid4()})
        self.queue.submit(self.command)
        self.queue.submit(other)
        values = self.client.lrange("flowledger:{jobs}:pending", 0, -1)
        self.assertEqual([WorkerCommand.model_validate_json(v) for v in values], [self.command, other])

    def test_bad_queue_type_does_not_leave_receipt(self):
        self.client.set("flowledger:{jobs}:pending", "wrong type")
        with self.assertRaises(ResponseError):
            self.queue.submit(self.command)
        self.assertFalse(self.client.exists("flowledger:{jobs}:receipts"))

    def test_connection_failure_is_not_success(self):
        self.server.connected = False
        with self.assertRaises(ConnectionError):
            self.queue.submit(self.command)
        self.server.connected = True
        self.assertTrue(self.queue.submit(self.command))

    def test_envelope_validation(self):
        valid = self.command.model_dump(mode="json")
        for field, value in [("schema_version", 2), ("expected_version", True),
                             ("expected_version", -1), ("command_id", "bad"),
                             ("target", "email_everyone"), ("extra", 1)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                WorkerCommand.model_validate({**valid, field: value})
