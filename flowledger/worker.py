"""Bounded database-only worker execution; no external notifications."""
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from flowledger.claims import MalformedCommandQuarantined, RedisClaimQueue
from flowledger.jobs import SubmissionConflict
from flowledger.repository import SqlAlchemyRepository
from sqlalchemy.exc import IntegrityError, OperationalError


class StopSignal(Protocol):
    """Small threading.Event-compatible boundary used by the worker loop."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


@dataclass(frozen=True)
class WorkerLoopConfig:
    work_batch_size: int = 100
    maintenance_batch_size: int = 100
    lease_ms: int = 30_000
    idle_wait_seconds: float = 1.0

    def __post_init__(self):
        for value in (self.work_batch_size, self.maintenance_batch_size):
            if type(value) is not int or not 1 <= value <= 1_000:
                raise ValueError("Batch sizes must be integers between 1 and 1000")
        if type(self.lease_ms) is not int or not 1 <= self.lease_ms <= 2_147_483_647:
            raise ValueError("Lease must be a positive 32-bit integer")
        wait = self.idle_wait_seconds
        if type(wait) not in (int, float) or not isfinite(wait) or not 0 < wait <= 3_600:
            raise ValueError("Idle wait must be a finite number between 0 and 3600 seconds")


@dataclass(frozen=True)
class WorkerLoopStats:
    cycles: int = 0
    commands_handled: int = 0
    recovered_claims: int = 0
    promoted_commands: int = 0


def run_once(queue: RedisClaimQueue, repository: SqlAlchemyRepository,
             lease_ms: int = 30_000) -> bool:
    """Commit before acknowledgement; back off reported database failures.

    False means idle. An expired acknowledgement is safe: redelivery checks the
    durable receipt. Recovery and due promotion must run periodically, even idle.
    Unexpected errors propagate and leave the claim for expiry recovery.
    """
    try:
        claim = queue.claim(lease_ms=lease_ms)
    except MalformedCommandQuarantined:
        return True
    if claim is None:
        return False
    try:
        repository.execute(claim.command, reject_invalid=True)
    except (OperationalError, IntegrityError):
        # execute has exited its transaction and rolled back. Do not retry within
        # a failed session, acknowledge failed work, or sleep holding a claim.
        queue.retry(claim.token)
        return True
    except SubmissionConflict:
        queue.quarantine(claim.token, "command_id_conflict")
        return True
    queue.acknowledge(claim.token)
    return True


def run_loop(queue: RedisClaimQueue, repository: SqlAlchemyRepository,
             stop: StopSignal, config: WorkerLoopConfig | None = None) -> WorkerLoopStats:
    """Run bounded maintenance and work cycles until graceful shutdown.

    The stop signal is checked between claims, never during a database transaction.
    Queue/Redis and unexpected repository errors propagate so an external process
    supervisor can restart the worker instead of hiding infrastructure failures.
    """
    config = config if config is not None else WorkerLoopConfig()
    cycles = handled = recovered = promoted = 0
    while not stop.is_set():
        cycles += 1
        recovered += queue.recover_expired(limit=config.maintenance_batch_size)
        promoted += queue.promote_due(limit=config.maintenance_batch_size)

        cycle_handled = 0
        while cycle_handled < config.work_batch_size and not stop.is_set():
            if not run_once(queue, repository, lease_ms=config.lease_ms):
                break
            cycle_handled += 1
            handled += 1

        if stop.is_set():
            break
        if cycle_handled < config.work_batch_size:
            stop.wait(config.idle_wait_seconds)

    return WorkerLoopStats(cycles, handled, recovered, promoted)
