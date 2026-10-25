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
    actor_id: str


@dataclass(frozen=True, slots=True)
class Workflow:
    id: UUID
    name: str
    owner_id: str
    status: Status
    attempts: int
    max_attempts: int
    version: int
    events: tuple[Event, ...]

    @classmethod
    def create(cls, name: str, max_attempts: int = 3, *, owner_id: str = "system",
               actor_id: str | None = None) -> "Workflow":
        if not name.strip():
            raise ValueError("Workflow name must not be blank")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        cls._identity(owner_id, "owner_id")
        actor_id = owner_id if actor_id is None else actor_id
        cls._identity(actor_id, "actor_id")
        event = Event(Status.PENDING, datetime.now(timezone.utc), 0, actor_id)
        return cls(uuid4(), name.strip(), owner_id, Status.PENDING, 0, max_attempts, 0, (event,))

    def transition(self, target: Status, *, actor_id: str = "system") -> "Workflow":
        self._identity(actor_id, "actor_id")
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
        event = Event(target, datetime.now(timezone.utc), attempts, actor_id)
        return replace(self, status=target, attempts=attempts,
                       version=self.version + 1, events=(*self.events, event))

    @staticmethod
    def _identity(value: str, field: str) -> None:
        if (type(value) is not str or value != value.strip()
                or not 1 <= len(value) <= 100):
            raise ValueError(f"{field} must be 1-100 non-whitespace-padded characters")
