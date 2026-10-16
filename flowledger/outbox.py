"""At-least-once database-to-Redis relay; no command execution here."""
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from flowledger.database import OutboxRecord
from flowledger.jobs import WorkerCommand
from flowledger.repository import SessionFactory


class SubmissionQueue(Protocol):
    def submit(self, command: WorkerCommand) -> bool: ...


def relay_pending(sessions: SessionFactory, queue: SubmissionQueue, *, limit: int = 100) -> int:
    """Mark delivery only after Redis accepts or recognizes the identical command.

    A crash after submission leaves the row pending. Replaying its stable command ID
    is safe while Redis receipts survive. Concurrent relays may submit duplicates.
    Errors propagate and leave the current row pending; earlier rows stay committed.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
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
            queue.submit(command)
            row.delivered_at = datetime.now(timezone.utc)
        delivered += 1
    return delivered
