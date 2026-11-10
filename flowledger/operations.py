"""Read-only operational signals for the modular FlowLedger processes."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os

from redis import Redis
from sqlalchemy import create_engine, func, select

from flowledger.claims import QueueHealth, RedisClaimQueue
from flowledger.database import OutboxRecord
from flowledger.repository import SessionFactory, build_session_factory


@dataclass(frozen=True)
class OutboxHealth:
    pending: int
    oldest_age_seconds: int | None


@dataclass(frozen=True)
class OperationalHealth:
    outbox: OutboxHealth
    redis: QueueHealth


def outbox_health(sessions: SessionFactory, *, now: datetime | None = None) -> OutboxHealth:
    """Return undelivered count and oldest age without loading command payloads."""
    observed_at = now if now is not None else datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must include a timezone")
    with sessions() as session:
        pending, oldest = session.execute(
            select(func.count(OutboxRecord.command_id), func.min(OutboxRecord.created_at))
            .where(OutboxRecord.delivered_at.is_(None))
        ).one()
    if oldest is None:
        return OutboxHealth(int(pending), None)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    age = max(0, int((observed_at.astimezone(timezone.utc)
                      - oldest.astimezone(timezone.utc)).total_seconds()))
    return OutboxHealth(int(pending), age)


def health_snapshot(sessions: SessionFactory, queue: RedisClaimQueue, *,
                    count_limit: int = 10_000,
                    now: datetime | None = None) -> OperationalHealth:
    """Read database and Redis health; connection and schema errors propagate."""
    return OperationalHealth(outbox_health(sessions, now=now),
                             queue.health_snapshot(count_limit))


def main() -> int:
    database_url = os.getenv("FLOWLEDGER_DATABASE_URL", "").strip()
    redis_url = os.getenv("FLOWLEDGER_REDIS_URL", "").strip()
    if not database_url or not redis_url:
        raise ValueError("FLOWLEDGER_DATABASE_URL and FLOWLEDGER_REDIS_URL are required")
    try:
        count_limit = int(os.getenv("FLOWLEDGER_HEALTH_COUNT_LIMIT", "10000"))
    except ValueError as error:
        raise ValueError("FLOWLEDGER_HEALTH_COUNT_LIMIT must be an integer") from error
    sessions = build_session_factory(create_engine(database_url))
    snapshot = health_snapshot(sessions, RedisClaimQueue(Redis.from_url(redis_url)),
                               count_limit=count_limit)
    print(json.dumps(asdict(snapshot), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
