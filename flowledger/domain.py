"""Workflow rules independent of HTTP, databases, and worker infrastructure."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class Status(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Event:
    status: Status
    occurred_at: datetime
    attempt: int


@dataclass(frozen=True, slots=True)
class Workflow:
    id: UUID
    name: str
    status: Status
    attempts: int
    max_attempts: int
    version: int
    events: tuple[Event, ...]

    @classmethod
    def create(cls, name: str, max_attempts: int = 3) -> "Workflow":
        if not name.strip():
            raise ValueError("Workflow name must not be blank")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        event = Event(Status.PENDING, datetime.now(timezone.utc), 0)
        return cls(uuid4(), name.strip(), Status.PENDING, 0, max_attempts, 0, (event,))

    def transition(self, target: Status) -> "Workflow":
        allowed = {
            Status.PENDING: {Status.RUNNING, Status.CANCELLED},
            Status.RUNNING: {Status.SUCCEEDED, Status.FAILED},
            Status.FAILED: {Status.PENDING, Status.CANCELLED},
            Status.SUCCEEDED: set(),
            Status.CANCELLED: set(),
        }
        if not isinstance(target, Status) or target not in allowed[self.status]:
            raise InvalidTransition(f"Cannot move from {self.status} to {target}")
        if target == Status.PENDING and self.attempts >= self.max_attempts:
            raise InvalidTransition("Retry limit reached")
        attempts = self.attempts + (target == Status.RUNNING)
        event = Event(target, datetime.now(timezone.utc), attempts)
        return replace(self, status=target, attempts=attempts,
                       version=self.version + 1, events=(*self.events, event))
