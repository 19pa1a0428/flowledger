import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import fakeredis
from redis import Redis
from redis.exceptions import ResponseError

from flowledger.claims import RedisClaimQueue
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.client = fakeredis.FakeRedis()
        self.queue = RedisClaimQueue(self.client)
        self.command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)

    def submit(self):
        return RedisSubmissionQueue(self.client).submit(self.command)

    def expire(self, claim):
        # Deterministic fault injection, not a sleep or a production clock override.
        self.client.zadd('flowledger:{jobs}:deadlines', {claim.token: 0})

    def test_claim_ack_and_empty(self):
        self.assertIsNone(self.queue.claim())
        self.assertTrue(self.submit())
        claim = self.queue.claim()
        self.assertEqual(claim.command, self.command)
        self.assertIsNone(self.queue.claim())
        self.assertTrue(self.queue.acknowledge(claim.token))
        self.assertFalse(self.queue.acknowledge(claim.token))
        self.assertFalse(self.submit())
        self.assertEqual(self.queue.recover_expired(), 0)

    def test_crashed_worker_reclaimed_by_new_client(self):
        self.submit()
        old = self.queue.claim()
        self.expire(old)
        restarted = RedisClaimQueue(self.client)
        self.assertFalse(restarted.acknowledge(old.token))
        self.assertEqual(restarted.recover_expired(), 1)
        self.assertEqual(restarted.recover_expired(), 0)
        fresh = restarted.claim()
        self.assertEqual(fresh.command, old.command)
        self.assertNotEqual(fresh.token, old.token)
        self.assertFalse(restarted.acknowledge(old.token))
        self.assertTrue(restarted.acknowledge(fresh.token))

    def test_concurrent_claim_has_one_winner(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.queue.claim(), range(16)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_recovery_bounded_and_active_claim_preserved(self):
        claims = []
        for _ in range(3):
            self.command = self.command.model_copy(update={'command_id': uuid4()})
            self.submit()
            claims.append(self.queue.claim())
        for claim in claims[:2]:
            self.expire(claim)
        self.assertEqual(self.queue.recover_expired(limit=1), 1)
        self.assertEqual(self.queue.recover_expired(limit=1), 1)
        self.assertEqual(self.queue.recover_expired(), 0)
        self.assertTrue(self.queue.acknowledge(claims[2].token))

    def test_wrong_type_does_not_remove_pending(self):
        self.submit()
        self.client.set('flowledger:{jobs}:claims', 'bad')
        with self.assertRaises(ResponseError):
            self.queue.claim()
        self.assertEqual(self.client.llen('flowledger:{jobs}:pending'), 1)

    def test_validation(self):
        for value in [True, 0, -1, 1.5, 2**40]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.queue.claim(value)
                with self.assertRaises(ValueError):
                    self.queue.recover_expired(value)


@unittest.skipUnless(os.getenv('FLOWLEDGER_TEST_REDIS_URL'), 'Live Redis URL not configured')
class LiveClaimTests(ClaimTests):
    """Same contract against a dedicated disposable Redis database, never shared data."""
    def setUp(self):
        self.client = Redis.from_url(os.environ['FLOWLEDGER_TEST_REDIS_URL'])
        self.client.ping()
        self.keys = ['flowledger:{jobs}:' + suffix for suffix in
                     ['pending', 'receipts', 'claims', 'deadlines']]
        if self.client.exists(*self.keys):
            self.fail('Live tests require empty FlowLedger keys in a disposable database')
        self.addCleanup(self.client.close)
        self.addCleanup(self.client.delete, *self.keys)
        self.queue = RedisClaimQueue(self.client)
        self.command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)
