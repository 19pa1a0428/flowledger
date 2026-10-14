"""Validated commands and atomic Redis submission, not job execution."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from flowledger.domain import Status


class WorkerCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    command_id: UUID
    workflow_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    target: Status = Status.RUNNING


class SubmissionConflict(ValueError):
    """A command ID was reused with a different payload."""


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
        self._submit = client.register_script(_SUBMIT)

    def submit(self, command: WorkerCommand) -> bool:
        """True for new work, False for exact replay; Redis errors propagate."""
        result = self._submit(
            keys=["flowledger:{jobs}:receipts", "flowledger:{jobs}:pending"],
            args=[str(command.command_id), command.model_dump_json()],
        )
        if result == -1:
            raise SubmissionConflict("Command ID already has a different payload")
        return result == 1
