"""At-least-once database-to-Redis relay; no command execution here."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from flowledger.database import CommandReceipt, OutboxRecord
from flowledger.jobs import WorkerCommand
from flowledger.metrics import APPLICATION_METRICS, FlowLedgerMetrics
from flowledger.repository import SessionFactory


class SubmissionQueue(Protocol):
    def submit(self, command: WorkerCommand) -> bool: ...


class ReconciliationQueue(SubmissionQueue, Protocol):
    def receipt_payload(self, command_id: UUID) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Bounded evidence from one reconciliation scan."""

    scanned: int
    receipts_present: int
    missing_ids: tuple[UUID, ...]
    requeued_ids: tuple[UUID, ...]
    converged_ids: tuple[UUID, ...]
    conflict_ids: tuple[UUID, ...]


def relay_pending(sessions: SessionFactory, queue: SubmissionQueue, *, limit: int = 100,
                  metric_sink: FlowLedgerMetrics | None = None) -> int:
    """Mark delivery only after Redis accepts or recognizes the identical command.

    A crash after submission leaves the row pending. Replaying its stable command ID
    is safe while Redis receipts survive. Concurrent relays may submit duplicates.
    Errors propagate and leave the current row pending; earlier rows stay committed.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    metric_sink = APPLICATION_METRICS if metric_sink is None else metric_sink
    with sessions() as session:
        ids = list(session.scalars(
            select(OutboxRecord.command_id)
            .where(OutboxRecord.delivered_at.is_(None))
            .order_by(OutboxRecord.created_at, OutboxRecord.command_id).limit(limit)
        ))
    delivered = 0
    for command_id in ids:
        with sessions.begin() as session:
            row = session.get(OutboxRecord, command_id)
            if row is None or row.delivered_at is not None:
                continue
            command = WorkerCommand.model_validate_json(row.payload)
            if command.command_id != row.command_id:
                raise ValueError("Outbox command ID does not match payload")
            try:
                submitted = queue.submit(command)
            except Exception:
                metric_sink.relay("error")
                raise
            metric_sink.relay("submitted" if submitted else "replayed")
            row.delivered_at = datetime.now(timezone.utc)
        delivered += 1
    return delivered


def reconcile_delivered(sessions: SessionFactory, queue: ReconciliationQueue, *,
                        requeue_missing: bool = False,
                        limit: int = 100) -> ReconciliationReport:
    """Inspect unresolved delivered rows and optionally restore missing submissions.

    Rows with durable database execution receipts are deliberately excluded: their
    transition or terminal rejection already committed, so Redis reconstruction would
    add needless work. The default is read-only. Applying repair resubmits the original
    stable command ID; database receipts remain the duplicate-effect safeguard.
    """
    if type(requeue_missing) is not bool:
        raise TypeError("requeue_missing must be a boolean")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")

    with sessions() as session:
        rows = list(session.scalars(
            select(OutboxRecord)
            .outerjoin(CommandReceipt, CommandReceipt.command_id == OutboxRecord.command_id)
            .where(OutboxRecord.delivered_at.is_not(None),
                   CommandReceipt.command_id.is_(None))
            .order_by(OutboxRecord.created_at, OutboxRecord.command_id)
            .limit(limit)
        ))

    present = 0
    missing: list[UUID] = []
    requeued: list[UUID] = []
    converged: list[UUID] = []
    conflicts: list[UUID] = []
    for row in rows:
        command = WorkerCommand.model_validate_json(row.payload)
        if command.command_id != row.command_id:
            raise ValueError("Outbox command ID does not match payload")
        receipt = queue.receipt_payload(row.command_id)
        if receipt is not None:
            if receipt == row.payload:
                present += 1
            else:
                conflicts.append(row.command_id)
            continue

        missing.append(row.command_id)
        if requeue_missing:
            if queue.submit(command):
                requeued.append(row.command_id)
            else:
                # Another reconciler installed the identical receipt after our read.
                converged.append(row.command_id)

    return ReconciliationReport(
        scanned=len(rows),
        receipts_present=present,
        missing_ids=tuple(missing),
        requeued_ids=tuple(requeued),
        converged_ids=tuple(converged),
        conflict_ids=tuple(conflicts),
    )
