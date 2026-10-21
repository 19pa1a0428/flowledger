"""One bounded poll; database-only effects, no external notifications."""
from flowledger.claims import MalformedCommandQuarantined, RedisClaimQueue
from flowledger.jobs import SubmissionConflict
from flowledger.repository import SqlAlchemyRepository
from sqlalchemy.exc import IntegrityError, OperationalError


def run_once(queue: RedisClaimQueue, repository: SqlAlchemyRepository) -> bool:
    """Commit before acknowledgement; back off reported database failures.

    False means idle. An expired acknowledgement is safe: redelivery checks the
    durable receipt. Recovery and due promotion must run periodically, even idle.
    Unexpected errors propagate and leave the claim for expiry recovery.
    """
    try:
        claim = queue.claim()
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
