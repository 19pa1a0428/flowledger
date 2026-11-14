"""Opt-in local monitor for payload-free operational alert signals."""
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import os
import signal
from threading import Event

from redis import Redis
from sqlalchemy import create_engine

from flowledger.alerting import AlertPolicy, AlertPolicyEvaluator, AlertSample
from flowledger.claims import RedisClaimQueue
from flowledger.operations import OperationalHealth, health_snapshot
from flowledger.repository import build_session_factory


class CappedTerminalCounts(ValueError):
    """Terminal queue growth cannot be derived from capped counts."""


@dataclass(frozen=True)
class MonitorConfig:
    database_url: str
    redis_url: str
    interval_seconds: float = 30.0
    count_limit: int = 10_000
    max_consecutive_errors: int = 3
    max_samples: int = 0
    policy: AlertPolicy = field(default_factory=AlertPolicy)

    def __post_init__(self) -> None:
        if not self.database_url or not self.redis_url:
            raise ValueError("FLOWLEDGER_DATABASE_URL and FLOWLEDGER_REDIS_URL are required")
        if (isinstance(self.interval_seconds, bool) or
                not isinstance(self.interval_seconds, (int, float)) or
                not math.isfinite(self.interval_seconds) or
                not 1 <= self.interval_seconds <= 3_600):
            raise ValueError("monitor interval must be between 1 and 3600 seconds")
        if type(self.count_limit) is not int or not 1 <= self.count_limit <= 2_147_483_647:
            raise ValueError("monitor count limit must be a positive 32-bit integer")
        if (type(self.max_consecutive_errors) is not int or
                not 1 <= self.max_consecutive_errors <= 100):
            raise ValueError("max consecutive errors must be between 1 and 100")
        if type(self.max_samples) is not int or not 0 <= self.max_samples <= 1_000_000:
            raise ValueError("max samples must be between 0 and 1000000")

    @classmethod
    def from_environment(cls) -> "MonitorConfig":
        database_url = os.getenv("FLOWLEDGER_DATABASE_URL", "").strip()
        redis_url = os.getenv("FLOWLEDGER_REDIS_URL", "").strip()
        try:
            outbox_age_seconds = int(
                os.getenv("FLOWLEDGER_ALERT_OUTBOX_AGE_SECONDS", "300")
            )
            terminal_growth = int(os.getenv("FLOWLEDGER_ALERT_TERMINAL_GROWTH", "1"))
            trigger_samples = int(os.getenv("FLOWLEDGER_ALERT_TRIGGER_SAMPLES", "3"))
            recovery_samples = int(os.getenv("FLOWLEDGER_ALERT_RECOVERY_SAMPLES", "2"))
            interval_seconds = float(os.getenv("FLOWLEDGER_MONITOR_INTERVAL_SECONDS", "30"))
            count_limit = int(os.getenv("FLOWLEDGER_MONITOR_COUNT_LIMIT", "10000"))
            max_consecutive_errors = int(
                os.getenv("FLOWLEDGER_MONITOR_MAX_CONSECUTIVE_ERRORS", "3")
            )
            max_samples = int(os.getenv("FLOWLEDGER_MONITOR_MAX_SAMPLES", "0"))
        except ValueError as error:
            raise ValueError("monitor tuning values must be numeric") from error
        policy = AlertPolicy(outbox_age_seconds, terminal_growth,
                             trigger_samples, recovery_samples)
        return cls(database_url, redis_url, interval_seconds, count_limit,
                   max_consecutive_errors, max_samples, policy)


def _alert_sample(snapshot: OperationalHealth, observed_at: datetime) -> AlertSample:
    terminal = (snapshot.redis.quarantined, snapshot.redis.exhausted)
    if any(count.capped for count in terminal):
        raise CappedTerminalCounts("terminal counts must be exact")
    return AlertSample(
        observed_at,
        snapshot.outbox.oldest_age_seconds,
        snapshot.redis.quarantined.value,
        snapshot.redis.exhausted.value,
    )


def _emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True), flush=True)


def run_monitor(snapshot_fn: Callable[[datetime], OperationalHealth],
                stop: Event, config: MonitorConfig, *,
                emit: Callable[[dict], None] = _emit,
                clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                wait: Callable[[float], bool] | None = None) -> int:
    """Sample until stopped; emit fixed, payload-free events and bounded errors."""
    evaluator = AlertPolicyEvaluator(config.policy)
    wait = stop.wait if wait is None else wait
    attempts = successes = errors = consecutive_errors = 0
    reason = "signal"

    while not stop.is_set():
        observed_at = clock()
        attempts += 1
        try:
            snapshot = snapshot_fn(observed_at)
            evaluation = evaluator.update(_alert_sample(snapshot, observed_at))
            consecutive_errors = 0
            successes += 1
            emit({
                "event": "sample",
                "outbox_pending": snapshot.outbox.pending,
                **asdict(evaluation),
            })
        except Exception as error:  # Data-source/schema failures are operational evidence.
            errors += 1
            consecutive_errors += 1
            emit({
                "consecutive_errors": consecutive_errors,
                "error_type": type(error).__name__,
                "event": "sample_error",
                "observed_at": observed_at.isoformat(),
            })
            if consecutive_errors >= config.max_consecutive_errors:
                reason = "consecutive_sample_errors"
                emit({"attempts": attempts, "errors": errors, "event": "stopped",
                      "reason": reason, "samples": successes})
                return 1

        if config.max_samples and attempts >= config.max_samples:
            reason = "max_samples"
            break
        if wait(config.interval_seconds):
            reason = "signal"
            break

    emit({"attempts": attempts, "errors": errors, "event": "stopped",
          "reason": reason, "samples": successes})
    return 0


def _stop_event() -> Event:
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    return stop


def main() -> int:
    config = MonitorConfig.from_environment()
    sessions = build_session_factory(create_engine(config.database_url))
    queue = RedisClaimQueue(Redis.from_url(config.redis_url))
    return run_monitor(
        lambda observed_at: health_snapshot(
            sessions, queue, count_limit=config.count_limit, now=observed_at,
        ),
        _stop_event(),
        config,
    )


if __name__ == "__main__":
    raise SystemExit(main())
