"""Dry-run or repair delivered outbox rows after Redis submission-state loss."""

from argparse import ArgumentParser
import json
import os

from redis import Redis
from sqlalchemy import create_engine

from flowledger.jobs import RedisSubmissionQueue
from flowledger.outbox import ReconciliationReport, reconcile_delivered
from flowledger.repository import build_session_factory


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _output(report: ReconciliationReport, *, requeue_missing: bool) -> str:
    return json.dumps({
        "mode": "requeue-missing" if requeue_missing else "dry-run",
        "scanned_unexecuted_deliveries": report.scanned,
        "redis_receipts_present": report.receipts_present,
        "missing_command_ids": [str(value) for value in report.missing_ids],
        "requeued_command_ids": [str(value) for value in report.requeued_ids],
        "converged_command_ids": [str(value) for value in report.converged_ids],
        "conflicting_command_ids": [str(value) for value in report.conflict_ids],
    }, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(
        description="Inspect unresolved delivered outbox rows; repair is explicit",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--requeue-missing",
        action="store_true",
        help="resubmit missing stable command IDs; omit for a read-only dry run",
    )
    args = parser.parse_args(argv)

    database_url = _required_environment("FLOWLEDGER_DATABASE_URL")
    redis_url = _required_environment("FLOWLEDGER_REDIS_URL")
    engine = create_engine(database_url)
    try:
        report = reconcile_delivered(
            build_session_factory(engine),
            RedisSubmissionQueue(Redis.from_url(redis_url)),
            requeue_missing=args.requeue_missing,
            limit=args.limit,
        )
    finally:
        engine.dispose()
    print(_output(report, requeue_missing=args.requeue_missing))
    return 2 if report.conflict_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
