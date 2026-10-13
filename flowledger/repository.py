"""Transactional persistence for workflow snapshots and their event history."""
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from flowledger.database import EventRecord, WorkflowRecord
from flowledger.domain import Event, Status, Workflow


SessionFactory = Callable[[], Session]


class ConcurrencyConflict(RuntimeError):
    """The workflow changed after the caller read it."""


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

    def add(self, workflow: Workflow) -> Workflow:
        record = WorkflowRecord(
            id=workflow.id, name=workflow.name, status=workflow.status.value,
            attempts=workflow.attempts, max_attempts=workflow.max_attempts,
            version=workflow.version,
            created_at=workflow.events[0].occurred_at,
            updated_at=workflow.events[-1].occurred_at,
            events=[EventRecord(position=position, status=event.status.value,
                                occurred_at=event.occurred_at, attempt=event.attempt)
                    for position, event in enumerate(workflow.events)],
        )
        with self._sessions.begin() as session:
            session.add(record)
        return workflow

    def get(self, workflow_id: UUID) -> Workflow:
        with self._sessions() as session:
            record = self._find(session, workflow_id)
            if record is None:
                raise KeyError(workflow_id)
            return self._to_domain(record)

    def transition(self, workflow_id: UUID, target: Status, expected_version: int) -> Workflow:
        with self._sessions.begin() as session:
            record = self._find(session, workflow_id)
            if record is None:
                raise KeyError(workflow_id)
            current = self._to_domain(record)
            if current.version != expected_version:
                raise ConcurrencyConflict(
                    f"Expected version {expected_version}, found {current.version}"
                )
            updated = current.transition(target)
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
                occurred_at=event.occurred_at, attempt=event.attempt,
            ))
        return updated

    @staticmethod
    def _find(session: Session, workflow_id: UUID) -> WorkflowRecord | None:
        statement = (select(WorkflowRecord).where(WorkflowRecord.id == workflow_id)
                     .options(selectinload(WorkflowRecord.events)))
        return session.scalar(statement)

    @staticmethod
    def _to_domain(record: WorkflowRecord) -> Workflow:
        events = tuple(Event(Status(event.status), _utc(event.occurred_at), event.attempt)
                       for event in record.events)
        return Workflow(id=record.id, name=record.name, status=Status(record.status),
                        attempts=record.attempts, max_attempts=record.max_attempts,
                        version=record.version,
                        events=events)
