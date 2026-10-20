"""At-least-once queue claims; this module does not execute workflow effects."""
from dataclasses import dataclass
from uuid import uuid4

from redis import Redis

from flowledger.jobs import WorkerCommand


@dataclass(frozen=True)
class RetryPolicy:
    """Budget counts reported failures/expiry, not workflow execution attempts."""

    max_failures: int = 3
    base_delay_ms: int = 1_000
    max_delay_ms: int = 60_000

    def __post_init__(self):
        for value in (self.max_failures, self.base_delay_ms, self.max_delay_ms):
            if type(value) is not int or not 1 <= value <= 2_147_483_647:
                raise ValueError("Retry values must be positive 32-bit integers")
        if self.max_failures > 100 or self.base_delay_ms > self.max_delay_ms:
            raise ValueError("Expected at most 100 failures and base delay <= maximum")


# Validate every key before mutation. Redis script errors are not rollbacks.
_PRELUDE = """
for i, expected in ipairs({'list', 'hash', 'zset', 'hash', 'zset', 'list'}) do
    local actual = redis.call('TYPE', KEYS[i]).ok
    if actual ~= 'none' and actual ~= expected then
        return redis.error_reply('Invalid claim key type')
    end
end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
"""
_CLAIM = _PRELUDE + """
local payload = redis.call('LPOP', KEYS[1])
if not payload then return nil end
redis.call('HSET', KEYS[2], ARGV[1], payload)
redis.call('ZADD', KEYS[3], now + tonumber(ARGV[2]), ARGV[1])
return payload
"""
_ACK = _PRELUDE + """
local deadline = redis.call('ZSCORE', KEYS[3], ARGV[1])
if not deadline or tonumber(deadline) <= now then return 0 end
local payload = redis.call('HGET', KEYS[2], ARGV[1])
if not payload then return redis.error_reply('Missing claimed payload') end
redis.call('HDEL', KEYS[4], payload)
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
return 1
"""
# Shared by explicit failure reporting and expired-lease recovery. Validate all
# counters in a recovery batch before mutation; Lua errors are not rollbacks.
_BACKOFF = """
local function failure_count(payload)
    local failures = tonumber(redis.call('HGET', KEYS[4], payload) or '0')
    if not failures or failures < 0 or failures > 100 or
       failures ~= math.floor(failures) then error('Invalid failure counter') end
    return failures + 1
end
local function reschedule(token, payload)
    local failures = failure_count(payload)
    local result = 1
    if failures >= tonumber(ARGV[2]) then
        redis.call('RPUSH', KEYS[6], payload)
        result = 2
    else
        local delay = math.min(tonumber(ARGV[4]),
                               tonumber(ARGV[3]) * 2 ^ (failures - 1))
        redis.call('ZADD', KEYS[5], now + delay, payload)
    end
    redis.call('HSET', KEYS[4], payload, failures)
    redis.call('HDEL', KEYS[2], token)
    redis.call('ZREM', KEYS[3], token)
    return result
end
"""
_RECOVER = _PRELUDE + _BACKOFF + """
local tokens = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now,
                          'LIMIT', 0, ARGV[1])
for _, token in ipairs(tokens) do
    local payload = redis.call('HGET', KEYS[2], token)
    if not payload then return redis.error_reply('Missing claimed payload') end
    failure_count(payload)
end
for _, token in ipairs(tokens) do
    reschedule(token, redis.call('HGET', KEYS[2], token))
end
return #tokens
"""
_RETRY = _PRELUDE + _BACKOFF + """
local deadline = redis.call('ZSCORE', KEYS[3], ARGV[1])
if not deadline or tonumber(deadline) <= now then return 0 end
local payload = redis.call('HGET', KEYS[2], ARGV[1])
if not payload then return redis.error_reply('Missing claimed payload') end
return reschedule(ARGV[1], payload)
"""
_PROMOTE = _PRELUDE + """
local payloads = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', now,
                            'LIMIT', 0, ARGV[1])
for _, payload in ipairs(payloads) do
    redis.call('RPUSH', KEYS[1], payload)
    redis.call('ZREM', KEYS[5], payload)
end
return #payloads
"""
_KEYS = ['flowledger:{jobs}:pending', 'flowledger:{jobs}:claims',
         'flowledger:{jobs}:deadlines', 'flowledger:{jobs}:failures',
         'flowledger:{jobs}:delayed', 'flowledger:{jobs}:exhausted']


@dataclass(frozen=True)
class Claim:
    token: str
    command: WorkerCommand


class RedisClaimQueue:
    def __init__(self, client: Redis, retry_policy: RetryPolicy | None = None):
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._claim = client.register_script(_CLAIM)
        self._ack = client.register_script(_ACK)
        self._recover = client.register_script(_RECOVER)
        self._retry = client.register_script(_RETRY)
        self._promote = client.register_script(_PROMOTE)

    def claim(self, lease_ms: int = 30_000) -> Claim | None:
        self._positive_int(lease_ms)
        token = str(uuid4())
        payload = self._claim(keys=_KEYS, args=[token, lease_ms])
        if payload is None:
            return None
        return Claim(token, WorkerCommand.model_validate_json(payload))

    def acknowledge(self, token: str) -> bool:
        """Reject unknown, expired, or already acknowledged claim tokens."""
        return bool(self._ack(keys=_KEYS, args=[token]))

    def recover_expired(self, limit: int = 100) -> int:
        """Back off or exhaust expired claims; call periodically while idle."""
        self._batch_size(limit)
        policy = self.retry_policy
        return int(self._recover(keys=_KEYS, args=[limit, policy.max_failures,
                            policy.base_delay_ms, policy.max_delay_ms]))

    def retry(self, token: str) -> str:
        """Atomically release an active claim into delayed or exhausted storage.

        Stale tokens cannot schedule work or consume another owner's budget.
        Redis errors propagate; an uncertain result is resolved by token replay.
        """
        policy = self.retry_policy
        result = int(self._retry(keys=_KEYS, args=[token, policy.max_failures,
                          policy.base_delay_ms, policy.max_delay_ms]))
        return {0: "stale", 1: "scheduled", 2: "exhausted"}[result]

    def promote_due(self, limit: int = 100) -> int:
        """Move a bounded due batch to pending; call periodically while idle."""
        self._batch_size(limit)
        return int(self._promote(keys=_KEYS, args=[limit]))

    @staticmethod
    def _batch_size(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("Expected a batch size between 1 and 1000")

    @staticmethod
    def _positive_int(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 2_147_483_647:
            raise ValueError('Expected a positive 32-bit integer')
