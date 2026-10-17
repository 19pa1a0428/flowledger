"""Transactional persistence for workflow snapshots and their event history."""
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from flowledger.database import EventRecord, OutboxRecord, WorkflowRecord
from flowledger.domain import Event, Status, Workflow
from flowledger.jobs import WorkerCommand


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

    def add(self, workflow: Workflow, command: WorkerCommand | None = None) -> Workflow:
        """Optionally persist initial execution intent alongside the new workflow."""
        if command is not None:
            if (command.workflow_id != workflow.id
                    or command.expected_version != workflow.version):
                raise ValueError("Command must reference the new workflow and its version")
            workflow.transition(command.target)  # Reject invalid intent before writing.
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

    def transition(self, workflow_id: UUID, target: Status, expected_version: int) -> Workflow:
        with self._sessions.begin() as session:
            return self._transition(session, workflow_id, target, expected_version)

    def execute(self, command: WorkerCommand) -> bool:
        """Atomically retain a receipt and apply one database-only transition.

        True means applied; False means identical replay. On a concurrent insert
        or version conflict the caller must retry the whole command in a new session.
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
            session.add(CommandReceipt(command_id=command.command_id, payload=payload))
            session.flush()  # Uniqueness arbitrates concurrent duplicate command IDs.
            self._transition(session, command.workflow_id, command.target,
                             command.expected_version)
        return True

    def _transition(self, session: Session, workflow_id: UUID, target: Status,
                    expected_version: int) -> Workflow:
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

