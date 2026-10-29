"""Transactional persistence for workflow snapshots and their event history."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import Engine, and_, or_, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from flowledger.database import EventRecord, OutboxRecord, WorkflowRecord
from flowledger.domain import Event, InvalidTransition, Status, Workflow
from flowledger.jobs import WorkerCommand


SessionFactory = Callable[[], Session]


class ConcurrencyConflict(RuntimeError):
    """The workflow changed after the caller read it."""


@dataclass(frozen=True)
class AuditEntry:
    event_id: int
    workflow_id: UUID
    position: int
    status: Status
    occurred_at: datetime
    attempt: int
    actor_id: str
    owner_id: str


@dataclass(frozen=True)
class AuditPage:
    items: tuple[AuditEntry, ...]
    next_cursor: int | None


@dataclass(frozen=True)
class WorkflowSummary:
    id: UUID
    name: str
    owner_id: str
    status: Status
    attempts: int
    max_attempts: int
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkflowPage:
    items: tuple[WorkflowSummary, ...]
    next_cursor: UUID | None


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def _utc(value: datetime) -> datetime:
    """SQLite returns naive values; the persisted contract is always UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SqlAlchemyRepository:
    """Stores each snapshot and its newly appended event in one transaction."""

    def __init__(self, sessions: SessionFactory):
        self._sessions = sessions

    def add(self, workflow: Workflow, command: WorkerCommand | None = None) -> Workflow:
        """Optionally persist initial execution intent alongside the new workflow."""
        if command is not None:
            if (command.workflow_id != workflow.id
                    or command.expected_version != workflow.version):
                raise ValueError("Command must reference the new workflow and its version")
            workflow.transition(command.target)  # Reject invalid intent before writing.
        record = WorkflowRecord(
            id=workflow.id, name=workflow.name, owner_id=workflow.owner_id,
            status=workflow.status.value,
            attempts=workflow.attempts, max_attempts=workflow.max_attempts,
            version=workflow.version,
            created_at=workflow.events[0].occurred_at,
            updated_at=workflow.events[-1].occurred_at,
            events=[EventRecord(position=position, status=event.status.value,
                                occurred_at=event.occurred_at, attempt=event.attempt,
                                actor_id=event.actor_id)
                    for position, event in enumerate(workflow.events)],
        )
        with self._sessions.begin() as session:
            session.add(record)
            if command is not None:
                session.add(OutboxRecord(
                    command_id=command.command_id, payload=command.model_dump_json(),
                    created_at=datetime.now(timezone.utc),
                ))
        return workflow

    def get(self, workflow_id: UUID) -> Workflow:
        with self._sessions() as session:
            record = self._find(session, workflow_id)
            if record is None:
                raise KeyError(workflow_id)
            return self._to_domain(record)

    def list_audit_events(self, *, limit: int = 50, cursor: int | None = None,
                          actor_id: str | None = None,
                          status: Status | None = None) -> AuditPage:
        """Return a stable newest-first page of persisted workflow events."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Audit page limit must be between 1 and 100")
        if cursor is not None and (type(cursor) is not int or cursor < 1):
            raise ValueError("Audit cursor must be a positive event ID")
        if actor_id is not None and (
            type(actor_id) is not str or actor_id != actor_id.strip()
            or not 1 <= len(actor_id) <= 100
        ):
            raise ValueError("Audit actor must be 1-100 non-whitespace-padded characters")
        if status is not None and not isinstance(status, Status):
            raise TypeError("Audit status must be a Status value")

        statement = (
            select(EventRecord, WorkflowRecord.owner_id)
            .join(WorkflowRecord, WorkflowRecord.id == EventRecord.workflow_id)
            .order_by(EventRecord.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(EventRecord.id < cursor)
        if actor_id is not None:
            statement = statement.where(EventRecord.actor_id == actor_id)
        if status is not None:
            statement = statement.where(EventRecord.status == status.value)

        with self._sessions() as session:
            rows = session.execute(statement).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(AuditEntry(
            event_id=event.id,
            workflow_id=event.workflow_id,
            position=event.position,
            status=Status(event.status),
            occurred_at=_utc(event.occurred_at),
            attempt=event.attempt,
            actor_id=event.actor_id,
            owner_id=owner_id,
        ) for event, owner_id in rows)
        return AuditPage(items, items[-1].event_id if has_more else None)

    def list_workflows(self, *, limit: int = 50, cursor: UUID | None = None,
                       owner_id: str | None = None,
                       status: Status | None = None) -> WorkflowPage:
        """Return a stable newest-first page, optionally restricted to one owner."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Workflow page limit must be between 1 and 100")
        if cursor is not None and not isinstance(cursor, UUID):
            raise TypeError("Workflow cursor must be a UUID")
        if owner_id is not None and (
            type(owner_id) is not str or owner_id != owner_id.strip()
            or not 1 <= len(owner_id) <= 100
        ):
            raise ValueError("Workflow owner must be 1-100 non-whitespace-padded characters")
        if status is not None and not isinstance(status, Status):
            raise TypeError("Workflow status must be a Status value")

        with self._sessions() as session:
            statement = (select(WorkflowRecord)
                         .order_by(WorkflowRecord.created_at.desc(),
                                   WorkflowRecord.id.desc())
                         .limit(limit + 1))
            if owner_id is not None:
                statement = statement.where(WorkflowRecord.owner_id == owner_id)
            if status is not None:
                statement = statement.where(WorkflowRecord.status == status.value)
            if cursor is not None:
                anchor = session.get(WorkflowRecord, cursor)
                if anchor is None or (owner_id is not None and anchor.owner_id != owner_id):
                    raise ValueError("Invalid workflow cursor")
                statement = statement.where(or_(
                    WorkflowRecord.created_at < anchor.created_at,
                    and_(WorkflowRecord.created_at == anchor.created_at,
                         WorkflowRecord.id < anchor.id),
                ))
            records = list(session.scalars(statement))

        has_more = len(records) > limit
        records = records[:limit]
        items = tuple(WorkflowSummary(
            id=record.id,
            name=record.name,
            owner_id=record.owner_id,
            status=Status(record.status),
            attempts=record.attempts,
            max_attempts=record.max_attempts,
            version=record.version,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        ) for record in records)
        return WorkflowPage(items, items[-1].id if has_more else None)

    def transition(self, workflow_id: UUID, target: Status, expected_version: int,
                   *, actor_id: str = "system") -> Workflow:
        with self._sessions.begin() as session:
            return self._transition(session, workflow_id, target, expected_version, actor_id)

    def execute(self, command: WorkerCommand, *, reject_invalid: bool = False) -> bool:
        """Atomically retain a receipt and apply one database-only transition.

        True means newly handled; False means identical replay. Workers may retain
        terminal domain rejections with reject_invalid=True. Infrastructure errors
        and command-ID payload conflicts still propagate and roll back.
        """
        from flowledger.database import CommandReceipt
        from flowledger.jobs import SubmissionConflict

        with self._sessions.begin() as session:
            receipt = session.get(CommandReceipt, command.command_id)
            payload = command.model_dump_json()
            if receipt is not None:
                if receipt.payload != payload:
                    raise SubmissionConflict("Command ID already executed with different payload")
                return False
            receipt = CommandReceipt(command_id=command.command_id, payload=payload)
            session.add(receipt)
            session.flush()  # Uniqueness arbitrates concurrent duplicate command IDs.
            try:
                # Isolate a rejected transition from its durable receipt. This also
                # protects history if transition implementation gains earlier writes.
                with session.begin_nested():
                    self._transition(session, command.workflow_id, command.target,
                                     command.expected_version, "worker")
            except (KeyError, ConcurrencyConflict, InvalidTransition) as error:
                if not reject_invalid:
                    raise
                receipt.rejection = (
                    "workflow_missing" if isinstance(error, KeyError) else
                    "stale_version" if isinstance(error, ConcurrencyConflict) else
                    "invalid_transition"
                )
        return True

    def _transition(self, session: Session, workflow_id: UUID, target: Status,
                    expected_version: int, actor_id: str) -> Workflow:
        record = self._find(session, workflow_id)
        if record is None:
            raise KeyError(workflow_id)
        current = self._to_domain(record)
        if current.version != expected_version:
            raise ConcurrencyConflict(
                f"Expected version {expected_version}, found {current.version}"
            )
        updated = current.transition(target, actor_id=actor_id)
        event = updated.events[-1]
        result = session.execute(
            update(WorkflowRecord)
            .where(WorkflowRecord.id == workflow_id,
                   WorkflowRecord.version == expected_version)
            .values(status=updated.status.value, attempts=updated.attempts,
                    version=updated.version, updated_at=event.occurred_at)
        )
        if result.rowcount != 1:
            raise ConcurrencyConflict(
                f"Workflow changed from version {expected_version}"
            )
        session.add(EventRecord(
            workflow_id=workflow_id,
            position=len(current.events), status=event.status.value,
            occurred_at=event.occurred_at, attempt=event.attempt, actor_id=event.actor_id,
        ))
        return updated

    @staticmethod
    def _find(session: Session, workflow_id: UUID) -> WorkflowRecord | None:
        statement = (select(WorkflowRecord).where(WorkflowRecord.id == workflow_id)
                     .options(selectinload(WorkflowRecord.events)))
        return session.scalar(statement)

    @staticmethod
    def _to_domain(record: WorkflowRecord) -> Workflow:
        events = tuple(Event(Status(event.status), _utc(event.occurred_at), event.attempt,
                             event.actor_id)
                       for event in record.events)
        return Workflow(id=record.id, name=record.name, owner_id=record.owner_id,
                        status=Status(record.status),
                        attempts=record.attempts, max_attempts=record.max_attempts,
                        version=record.version,
                        events=events)

