"""At-least-once queue claims; this module does not execute workflow effects."""
from dataclasses import dataclass
from uuid import uuid4

from redis import Redis

from flowledger.jobs import WorkerCommand


# Validate every key before mutation. Redis script errors are not rollbacks.
_PRELUDE = """
for i, expected in ipairs({'list', 'hash', 'zset'}) do
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
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
return 1
"""
_RECOVER = _PRELUDE + """
local tokens = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now,
                          'LIMIT', 0, ARGV[1])
for _, token in ipairs(tokens) do
    local payload = redis.call('HGET', KEYS[2], token)
    if not payload then return redis.error_reply('Missing claimed payload') end
end
for _, token in ipairs(tokens) do
    redis.call('RPUSH', KEYS[1], redis.call('HGET', KEYS[2], token))
    redis.call('HDEL', KEYS[2], token)
    redis.call('ZREM', KEYS[3], token)
end
return #tokens
"""
_KEYS = ['flowledger:{jobs}:pending', 'flowledger:{jobs}:claims',
         'flowledger:{jobs}:deadlines']


@dataclass(frozen=True)
class Claim:
    token: str
    command: WorkerCommand


class RedisClaimQueue:
    def __init__(self, client: Redis):
        self._claim = client.register_script(_CLAIM)
        self._ack = client.register_script(_ACK)
        self._recover = client.register_script(_RECOVER)

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
        """Requeue a bounded batch. Call periodically even when pending is empty."""
        self._positive_int(limit)
        return int(self._recover(keys=_KEYS, args=[limit]))

    @staticmethod
    def _positive_int(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 2_147_483_647:
            raise ValueError('Expected a positive 32-bit integer')
