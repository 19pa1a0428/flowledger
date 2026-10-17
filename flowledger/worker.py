"""One bounded poll; database-only effects, no external notifications."""
from flowledger.claims import RedisClaimQueue
from flowledger.repository import SqlAlchemyRepository


def run_once(queue: RedisClaimQueue, repository: SqlAlchemyRepository) -> bool:
    """Commit before acknowledgement; errors leave the claim recoverable.

    False means idle. An expired acknowledgement is safe: redelivery checks the
    durable receipt. Recovery must be called periodically by the caller.
    """
    claim = queue.claim()
    if claim is None:
        return False
    repository.execute(claim.command)
    queue.acknowledge(claim.token)
    return True
