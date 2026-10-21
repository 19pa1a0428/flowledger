import os
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import fakeredis
from redis import Redis
from redis.exceptions import ResponseError

from flowledger.claims import (MalformedCommandQuarantined, RedisClaimQueue,
                               RetryPolicy)
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
        self.assertIsNone(restarted.claim())
        self.client.zadd('flowledger:{jobs}:delayed', {old.command.model_dump_json(): 0})
        self.assertEqual(restarted.promote_due(), 1)
        fresh = restarted.claim()
        self.assertEqual(fresh.command, old.command)
        self.assertNotEqual(fresh.token, old.token)
        self.assertFalse(restarted.acknowledge(old.token))
        self.assertEqual(restarted.retry(old.token), 'stale')
        self.assertEqual(self.client.hget('flowledger:{jobs}:failures',
                                        old.command.model_dump_json()), b'1')
        self.assertTrue(restarted.acknowledge(fresh.token))

    def test_recovery_counter_error_does_not_partially_move_batch(self):
        claims = []
        for _ in range(2):
            self.command = self.command.model_copy(update={'command_id': uuid4()})
            self.submit()
            claims.append(self.queue.claim())
            self.expire(claims[-1])
        self.client.hset('flowledger:{jobs}:failures',
                         claims[1].command.model_dump_json(), 'bad')
        with self.assertRaises(ResponseError):
            self.queue.recover_expired()
        self.assertEqual(self.client.hlen('flowledger:{jobs}:claims'), 2)
        self.assertEqual(self.client.zcard('flowledger:{jobs}:delayed'), 0)

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

    def make_due(self):
        for payload in self.client.zrange('flowledger:{jobs}:delayed', 0, -1):
            self.client.zadd('flowledger:{jobs}:delayed', {payload: 0})

    def test_retry_backoff_cap_and_success_cleanup(self):
        policy = RetryPolicy(max_failures=5, base_delay_ms=60_000, max_delay_ms=90_000)
        self.queue = RedisClaimQueue(self.client, policy)
        self.submit()
        for delay in (60_000, 90_000, 90_000):
            claim = self.queue.claim()
            before = self.client.time()
            self.assertEqual(self.queue.retry(claim.token), 'scheduled')
            after = self.client.time()
            deadline = self.client.zscore('flowledger:{jobs}:delayed', self.command.model_dump_json())
            low = before[0] * 1000 + before[1] // 1000
            high = after[0] * 1000 + after[1] // 1000
            self.assertGreaterEqual(deadline, low + delay)
            self.assertLessEqual(deadline, high + delay)
            self.assertEqual(self.queue.retry(claim.token), 'stale')
            self.assertIsNone(self.queue.claim())
            self.assertEqual(self.queue.promote_due(), 0)
            self.make_due()
            self.assertEqual(self.queue.promote_due(), 1)
        claim = self.queue.claim()
        self.assertTrue(self.queue.acknowledge(claim.token))
        self.assertEqual(self.client.hlen('flowledger:{jobs}:failures'), 0)

    def test_retry_exhaustion_and_restart(self):
        self.queue = RedisClaimQueue(self.client, RetryPolicy(max_failures=2))
        self.submit()
        self.assertEqual(self.queue.retry(self.queue.claim().token), 'scheduled')
        # A fresh object has no in-memory retry state.
        restarted = RedisClaimQueue(self.client, RetryPolicy(max_failures=2))
        self.make_due()
        self.assertEqual(restarted.promote_due(), 1)
        claim = restarted.claim()
        self.assertEqual(restarted.retry(claim.token), 'exhausted')
        self.assertEqual(restarted.retry(claim.token), 'stale')
        self.assertEqual(self.client.lrange('flowledger:{jobs}:exhausted', 0, -1),
                         [self.command.model_dump_json().encode()])
        self.assertEqual(restarted.promote_due(), 0)
        self.assertIsNone(restarted.claim())

    def test_expired_claim_uses_failure_budget(self):
        self.queue = RedisClaimQueue(self.client, RetryPolicy(max_failures=1))
        self.submit()
        claim = self.queue.claim()
        self.expire(claim)
        self.assertEqual(self.queue.retry(claim.token), 'stale')
        self.assertEqual(self.queue.recover_expired(), 1)
        self.assertEqual(self.client.llen('flowledger:{jobs}:exhausted'), 1)
        self.assertIsNone(self.queue.claim())

    def test_concurrent_retry_and_promotion_are_single_winner(self):
        self.submit()
        claim = self.queue.claim()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.queue.retry(claim.token), range(8)))
        self.assertEqual(results.count('scheduled'), 1)
        self.assertEqual(results.count('stale'), 7)
        self.make_due()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.queue.promote_due(), range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.client.llen('flowledger:{jobs}:pending'), 1)

    def test_promotion_is_bounded(self):
        for _ in range(3):
            self.command = self.command.model_copy(update={'command_id': uuid4()})
            self.submit()
            self.queue.retry(self.queue.claim().token)
        self.make_due()
        self.assertEqual(self.queue.promote_due(limit=2), 2)
        self.assertEqual(self.queue.promote_due(limit=2), 1)

    def test_retry_type_and_counter_errors_preserve_claim(self):
        self.submit()
        claim = self.queue.claim()
        payload = self.command.model_dump_json()
        self.client.hset('flowledger:{jobs}:failures', payload, 'bad')
        with self.assertRaises(ResponseError):
            self.queue.retry(claim.token)
        self.assertTrue(self.client.hexists('flowledger:{jobs}:claims', claim.token))
        self.client.hdel('flowledger:{jobs}:failures', payload)
        self.client.set('flowledger:{jobs}:delayed', 'bad')
        with self.assertRaises(ResponseError):
            self.queue.retry(claim.token)
        self.assertTrue(self.client.hexists('flowledger:{jobs}:claims', claim.token))
        self.assertEqual(self.client.hlen('flowledger:{jobs}:failures'), 0)

    def test_retry_policy_and_batch_validation(self):
        for kwargs in ({'max_failures': True}, {'max_failures': 101},
                       {'base_delay_ms': 0}, {'max_delay_ms': 1.5},
                       {'base_delay_ms': 100, 'max_delay_ms': 10}):
            with self.assertRaises(ValueError):
                RetryPolicy(**kwargs)
        for value in (0, True, 1001, 1.5):
            with self.assertRaises(ValueError):
                self.queue.promote_due(value)

    def test_malformed_payload_is_quarantined_atomically(self):
        payloads = [b'not-json', b'{"schema_version":2}', b'{}']
        self.client.rpush('flowledger:{jobs}:pending', *payloads)
        for payload in payloads:
            with self.assertRaises(MalformedCommandQuarantined):
                self.queue.claim()
            record = json.loads(self.client.lindex('flowledger:{jobs}:quarantine', -1))
            self.assertEqual(record['payload'].encode(), payload)
            self.assertEqual(record['reason'], 'malformed_command')
            self.assertIsInstance(record['quarantined_at_ms'], int)
        self.assertEqual(self.client.hlen('flowledger:{jobs}:claims'), 0)
        self.assertEqual(self.client.zcard('flowledger:{jobs}:deadlines'), 0)
        self.assertEqual(self.queue.recover_expired(), 0)

    def test_quarantine_failure_preserves_claim(self):
        self.client.rpush('flowledger:{jobs}:pending', 'not-json')
        self.client.set('flowledger:{jobs}:quarantine', 'bad')
        with self.assertRaises(ResponseError):
            self.queue.claim()
        # Key types are validated before LPOP, so delivery remains pending.
        self.assertEqual(self.client.llen('flowledger:{jobs}:pending'), 1)
        self.assertEqual(self.client.hlen('flowledger:{jobs}:claims'), 0)
        self.assertEqual(self.client.zcard('flowledger:{jobs}:deadlines'), 0)

    def test_quarantine_rejects_stale_token_and_bad_reason(self):
        self.submit()
        claim = self.queue.claim()
        with self.assertRaises(ValueError):
            self.queue.quarantine(claim.token, 'unknown')
        self.assertTrue(self.queue.acknowledge(claim.token))
        self.assertFalse(self.queue.quarantine(claim.token, 'command_id_conflict'))


@unittest.skipUnless(os.getenv('FLOWLEDGER_TEST_REDIS_URL'), 'Live Redis URL not configured')
class LiveClaimTests(ClaimTests):
    """Same contract against a dedicated disposable Redis database, never shared data."""
    def setUp(self):
        self.client = Redis.from_url(os.environ['FLOWLEDGER_TEST_REDIS_URL'])
        self.client.ping()
        self.keys = ['flowledger:{jobs}:' + suffix for suffix in
                     ['pending', 'receipts', 'claims', 'deadlines', 'failures',
                      'delayed', 'exhausted', 'quarantine']]
        if self.client.exists(*self.keys):
            self.fail('Live tests require empty FlowLedger keys in a disposable database')
        self.addCleanup(self.client.close)
        self.addCleanup(self.client.delete, *self.keys)
        self.queue = RedisClaimQueue(self.client)
        self.command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)
