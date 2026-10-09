"""Temporary process-local storage; replace with transactional persistence."""
from threading import Lock
from uuid import UUID

from flowledger.domain import Status, Workflow


class InMemoryRepository:
    """One application instance only. Restarting discards all workflows."""

    def __init__(self):
        self._workflows: dict[UUID, Workflow] = {}
        self._lock = Lock()

    def add(self, workflow: Workflow) -> Workflow:
        with self._lock:
            self._workflows[workflow.id] = workflow
        return workflow

    def get(self, workflow_id: UUID) -> Workflow:
        with self._lock:
            return self._workflows[workflow_id]

    def transition(self, workflow_id: UUID, target: Status) -> Workflow:
        # Serialize read/transition/write within this process. This does not
        # coordinate multiple server processes or provide durable transactions.
        with self._lock:
            updated = self._workflows[workflow_id].transition(target)
            self._workflows[workflow_id] = updated
            return updated
