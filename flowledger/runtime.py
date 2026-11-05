"""Process entry points for the modular relay and worker services."""
from argparse import ArgumentParser
from dataclasses import dataclass
import os
import signal
from threading import Event

from redis import Redis
from sqlalchemy import create_engine

from flowledger.claims import RedisClaimQueue
from flowledger.jobs import RedisSubmissionQueue
from flowledger.outbox import relay_pending
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from flowledger.worker import WorkerLoopConfig, run_loop


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    redis_url: str
    batch_size: int = 100
    idle_wait_seconds: float = 1.0
    lease_ms: int = 30_000

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        database_url = os.getenv("FLOWLEDGER_DATABASE_URL", "").strip()
        redis_url = os.getenv("FLOWLEDGER_REDIS_URL", "").strip()
        if not database_url or not redis_url:
            raise ValueError("FLOWLEDGER_DATABASE_URL and FLOWLEDGER_REDIS_URL are required")
        try:
            batch_size = int(os.getenv("FLOWLEDGER_BATCH_SIZE", "100"))
            idle_wait = float(os.getenv("FLOWLEDGER_IDLE_WAIT_SECONDS", "1"))
            lease_ms = int(os.getenv("FLOWLEDGER_LEASE_MS", "30000"))
        except ValueError as error:
            raise ValueError("Runtime tuning values must be numeric") from error
        # Reuse the worker's bounded validation for every long-running process.
        WorkerLoopConfig(batch_size, batch_size, lease_ms, idle_wait)
        return cls(database_url, redis_url, batch_size, idle_wait, lease_ms)


def run_relay_loop(sessions, queue, stop, *, batch_size: int = 100,
                   idle_wait_seconds: float = 1.0) -> None:
    """Relay bounded outbox batches until a graceful shutdown is requested."""
    WorkerLoopConfig(batch_size, batch_size, 30_000, idle_wait_seconds)
    while not stop.is_set():
        delivered = relay_pending(sessions, queue, limit=batch_size)
        if delivered < batch_size:
            stop.wait(idle_wait_seconds)


def _stop_event() -> Event:
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    return stop


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Run a FlowLedger background process")
    parser.add_argument("service", choices=("relay", "worker"))
    args = parser.parse_args(argv)
    config = RuntimeConfig.from_environment()
    sessions = build_session_factory(create_engine(config.database_url))
    client = Redis.from_url(config.redis_url)
    stop = _stop_event()
    if args.service == "relay":
        run_relay_loop(sessions, RedisSubmissionQueue(client), stop,
                       batch_size=config.batch_size,
                       idle_wait_seconds=config.idle_wait_seconds)
    else:
        run_loop(RedisClaimQueue(client), SqlAlchemyRepository(sessions), stop,
                 WorkerLoopConfig(config.batch_size, config.batch_size,
                                  config.lease_ms, config.idle_wait_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
