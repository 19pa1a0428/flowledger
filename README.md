# FlowLedger

A portfolio project for reliable workflow execution and notifications.

The planned system lets operators create workflows, schedule synthetic notifications,
inspect failures, and retry work without duplicating effects. The focus is backend
correctness, operational visibility, and clear engineering tradeoffs.

## Current implementation

- Immutable workflow snapshots and UTC event history.
- Explicit state transitions and bounded retry attempts.
- FastAPI create/read/transition endpoints with contract tests.
- SQLAlchemy workflow/event models and a reversible Alembic migration.
- Transactional SQLAlchemy repository wired into the API.
- Optimistic concurrency control for stale transition requests.
- PostgreSQL-native UUID columns, ordered event history, foreign keys, and database checks.
- A GitHub Actions test matrix for Python 3.11–3.13.

Workflow snapshots and their appended events commit atomically and survive process
restarts. Each transition supplies the version it read. A conditional database update
advances that version, so a stale writer receives `409 Conflict` without overwriting the
newer snapshot or appending a misleading event.

## Run the tests

Requires Python 3.11 or newer:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The migration tests use a temporary SQLite database so they run without external
infrastructure. They verify upgrade, constraints, relationships, and downgrade.
PostgreSQL DDL can be reviewed without connecting to a server:

```bash
python -m alembic upgrade head --sql
```

For a local PostgreSQL instance, create the schema and point the API at the same URL:

```bash
python -m alembic upgrade head
export FLOWLEDGER_DATABASE_URL=postgresql+psycopg://flowledger:flowledger@localhost/flowledger
uvicorn flowledger.api:app --reload --host 127.0.0.1
```

Do not use the checked-in example credentials outside local development. A later
Docker increment will provide a reproducible local database environment.

## Design

A workflow starts pending, then runs and either succeeds or fails. Failed work may
return to pending while its execution budget remains. Pending or failed work may be
cancelled. Success and cancellation are terminal. `max_attempts` includes the
initial execution rather than counting only retries.

Event rows use a per-workflow `position` with a uniqueness constraint. This preserves
deterministic ordering even when timestamps are equal. Database checks protect core
status and retry invariants from writes that bypass the application.

See [the roadmap](docs/ROADMAP.md) and [the learning journal](docs/LEARNING.md).

## Try the API

```bash
python -m alembic upgrade head
uvicorn flowledger.api:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/docs. The current routes are `POST /workflows`,
`GET /workflows/{id}`, and `POST /workflows/{id}/transitions`.
The transition body is shaped like
`{"status": "running", "expected_version": 0}`. After a successful transition, use
the returned `version` for the next request; on `409`, fetch the latest workflow before
deciding whether to retry.

## Redis command submission

`flowledger.jobs.WorkerCommand` carries a schema version, command UUID, workflow
UUID, expected workflow version, and target status. `RedisSubmissionQueue.submit`
returns True for new work, False for an identical replay, and raises
`SubmissionConflict` when an ID is reused with different contents.

```python
from uuid import uuid4
from redis import Redis
from flowledger.jobs import RedisSubmissionQueue, WorkerCommand

queue = RedisSubmissionQueue(Redis.from_url("redis://localhost:6379/0"))
command = WorkerCommand(command_id=uuid4(), workflow_id=uuid4(), expected_version=0)
assert queue.submit(command) is True
assert queue.submit(command) is False
```

The workflow ID above is synthetic; submission does not check database existence.
A Lua script serializes receipt checking and list append. Both keys share a cluster
hash tag. Receipts have no TTL: expiring them would allow late duplicate submissions,
but indefinite retention grows memory. This is a submission library, not an API route
or executing worker. No real notifications are sent.

Use a dedicated local Redis with persistence and no eviction when experimenting.
Deduplication lasts only while receipts survive: Redis loss, eviction, script resource
errors, or manual deletion invalidate that guarantee. Lua does not roll back writes
on an error; key types are checked before mutation, but memory/resource failures
remain a limitation. This is not exactly-once execution or a database/Redis transaction.
Reliable claim/ack, live-engine tests, and an outbox are next. Tests currently use
fakeredis with Lua support, without requiring a live Redis server.

## Remaining stack

FastAPI, PostgreSQL, SQLAlchemy/Alembic, a Redis-backed worker, React/TypeScript,
Docker, GitHub Actions, and OpenTelemetry. Start as a modular application with a
worker; split services only for a demonstrated need.

