"""At-least-once queue claims; this module does not execute workflow effects."""
from dataclasses import dataclass
import json
from uuid import uuid4

from redis import Redis
from pydantic import ValidationError

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


class MalformedCommandQuarantined(ValueError):
    """The claimed payload was retained in quarantine instead of redelivered."""


class InspectionDataError(ValueError):
    """Retained operational data does not match the inspection contract."""


# Validate every key before mutation. Redis script errors are not rollbacks.
_PRELUDE = """
for i, expected in ipairs({'list', 'hash', 'zset', 'hash', 'zset', 'list', 'list'}) do
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
_QUARANTINE = _PRELUDE + """
local deadline = redis.call('ZSCORE', KEYS[3], ARGV[1])
if not deadline or tonumber(deadline) <= now then return 0 end
local payload = redis.call('HGET', KEYS[2], ARGV[1])
if not payload then return redis.error_reply('Missing claimed payload') end
local record = cjson.encode({payload=payload, reason=ARGV[2], quarantined_at_ms=now})
redis.call('RPUSH', KEYS[7], record)
redis.call('HDEL', KEYS[4], payload)
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
return 1
"""
_KEYS = ['flowledger:{jobs}:pending', 'flowledger:{jobs}:claims',
         'flowledger:{jobs}:deadlines', 'flowledger:{jobs}:failures',
         'flowledger:{jobs}:delayed', 'flowledger:{jobs}:exhausted',
         'flowledger:{jobs}:quarantine']


@dataclass(frozen=True)
class Claim:
    token: str
    command: WorkerCommand


@dataclass(frozen=True)
class QuarantinedCommand:
    payload: str
    reason: str
    quarantined_at_ms: int


@dataclass(frozen=True)
class ExhaustedCommand:
    payload: str
    failure_count: int


class RedisClaimQueue:
    def __init__(self, client: Redis, retry_policy: RetryPolicy | None = None):
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._client = client
        self._claim = client.register_script(_CLAIM)
        self._ack = client.register_script(_ACK)
        self._recover = client.register_script(_RECOVER)
        self._retry = client.register_script(_RETRY)
        self._promote = client.register_script(_PROMOTE)
        self._quarantine = client.register_script(_QUARANTINE)

    def claim(self, lease_ms: int = 30_000) -> Claim | None:
        self._positive_int(lease_ms)
        token = str(uuid4())
        payload = self._claim(keys=_KEYS, args=[token, lease_ms])
        if payload is None:
            return None
        try:
            command = WorkerCommand.model_validate_json(payload)
        except ValidationError as error:
            if not self.quarantine(token, "malformed_command"):
                raise RuntimeError("Claim expired before malformed payload quarantine") from error
            raise MalformedCommandQuarantined("Malformed command moved to quarantine") from error
        return Claim(token, command)

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

    def quarantine(self, token: str, reason: str) -> bool:
        """Atomically retain an active claim and remove it from delivery state."""
        if reason not in {"malformed_command", "command_id_conflict"}:
            raise ValueError("Unsupported quarantine reason")
        return bool(self._quarantine(keys=_KEYS, args=[token, reason]))

    def list_quarantined(self, offset: int = 0,
                         limit: int = 100) -> list[QuarantinedCommand]:
        """Read an oldest-first bounded page without changing delivery state."""
        self._page(offset, limit)
        records = self._client.lrange(_KEYS[6], offset, offset + limit - 1)
        result = []
        for raw in records:
            try:
                record = json.loads(self._text(raw))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise InspectionDataError("Invalid quarantine record encoding") from error
            if (not isinstance(record, dict)
                    or type(record.get("payload")) is not str
                    or type(record.get("reason")) is not str
                    or record["reason"] not in {"malformed_command", "command_id_conflict"}
                    or type(record.get("quarantined_at_ms")) is not int
                    or record["quarantined_at_ms"] < 0):
                raise InspectionDataError("Invalid quarantine record fields")
            result.append(QuarantinedCommand(record["payload"], record["reason"],
                                              record["quarantined_at_ms"]))
        return result

    def list_exhausted(self, offset: int = 0,
                       limit: int = 100) -> list[ExhaustedCommand]:
        """Read an oldest-first bounded page with its retained failure counts."""
        self._page(offset, limit)
        payloads = self._client.lrange(_KEYS[5], offset, offset + limit - 1)
        if not payloads:
            return []
        counters = self._client.hmget(_KEYS[3], payloads)
        result = []
        for raw_payload, raw_count in zip(payloads, counters, strict=True):
            try:
                count = int(self._text(raw_count))
            except (TypeError, UnicodeDecodeError, ValueError) as error:
                raise InspectionDataError("Invalid exhausted failure count") from error
            if not 1 <= count <= 100:
                raise InspectionDataError("Invalid exhausted failure count")
            try:
                payload = self._text(raw_payload)
            except UnicodeDecodeError as error:
                raise InspectionDataError("Invalid exhausted payload encoding") from error
            result.append(ExhaustedCommand(payload, count))
        return result

    @classmethod
    def _page(cls, offset: int, limit: int) -> None:
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            raise ValueError("Offset must be a nonnegative 32-bit integer")
        cls._batch_size(limit)

    @staticmethod
    def _text(value: bytes | str | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if isinstance(value, str):
            return value
        raise TypeError("Expected Redis text data")

    @staticmethod
    def _batch_size(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("Expected a batch size between 1 and 1000")

    @staticmethod
    def _positive_int(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 2_147_483_647:
            raise ValueError('Expected a positive 32-bit integer')
