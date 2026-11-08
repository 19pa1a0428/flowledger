"""Validated commands and atomic Redis submission, not job execution."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis import Redis

from flowledger.domain import Status


class WorkerCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    command_id: UUID
    workflow_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    target: Status = Status.RUNNING
    traceparent: str | None = Field(default=None, min_length=55, max_length=55)
    tracestate: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("traceparent")
    @classmethod
    def validate_traceparent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split("-")
        if (len(parts) != 4 or parts[0] != "00"
                or any(character not in "0123456789abcdef"
                       for part in parts for character in part)
                or len(parts[1]) != 32 or len(parts[2]) != 16 or len(parts[3]) != 2
                or int(parts[1], 16) == 0 or int(parts[2], 16) == 0):
            raise ValueError("Invalid W3C traceparent")
        return value

    @field_validator("tracestate")
    @classmethod
    def validate_tracestate(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) > 126 for character in value)
        ):
            raise ValueError("Invalid W3C tracestate")
        return value


class SubmissionConflict(ValueError):
    """A command ID was reused with a different payload."""


RECEIPTS_KEY = "flowledger:{jobs}:receipts"
PENDING_KEY = "flowledger:{jobs}:pending"


# Both keys share a Redis Cluster hash tag. Check types before writing because
# Redis does not roll back earlier Lua writes when a later command errors.
_SUBMIT = """
local receipts = redis.call('TYPE', KEYS[1]).ok
local queue = redis.call('TYPE', KEYS[2]).ok
if receipts ~= 'none' and receipts ~= 'hash' then
    return redis.error_reply('Invalid receipt key type')
end
if queue ~= 'none' and queue ~= 'list' then
    return redis.error_reply('Invalid queue key type')
end
local existing = redis.call('HGET', KEYS[1], ARGV[1])
if existing then
    if existing == ARGV[2] then return 0 end
    return -1
end
redis.call('RPUSH', KEYS[2], ARGV[2])
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""


class RedisSubmissionQueue:
    """Retain receipts indefinitely; Redis persistence is an operator concern."""

    def __init__(self, client: Redis):
        self._client = client
        self._submit = client.register_script(_SUBMIT)

    def submit(self, command: WorkerCommand) -> bool:
        """True for new work, False for exact replay; Redis errors propagate."""
        result = self._submit(
            keys=[RECEIPTS_KEY, PENDING_KEY],
            args=[str(command.command_id), command.model_dump_json()],
        )
        if result == -1:
            raise SubmissionConflict("Command ID already has a different payload")
        return result == 1

    def receipt_payload(self, command_id: UUID) -> str | None:
        """Return the retained submission payload without mutating queue state."""
        if not isinstance(command_id, UUID):
            raise TypeError("command_id must be a UUID")
        payload = self._client.hget(RECEIPTS_KEY, str(command_id))
        if payload is None or isinstance(payload, str):
            return payload
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        raise TypeError("Redis receipt payload must be bytes or text")
