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
Queue claims and recovery are available through `flowledger.claims.RedisClaimQueue`:

```python
from flowledger.claims import RedisClaimQueue

worker_queue = RedisClaimQueue(Redis.from_url("redis://localhost:6379/0"))
worker_queue.recover_expired(limit=100)  # Call periodically, including when idle.
claim = worker_queue.claim(lease_ms=30_000)
if claim is not None:
    print(claim.command)  # Inspection only: no workflow effects executed here.
    assert worker_queue.acknowledge(claim.token)
```

Claiming atomically moves one pending payload into a token-indexed hash and lease
deadline set. Redis server time determines expiry. Recovery requeues expired claims
in bounded batches; each new claim receives a new token. Acknowledgements for old,
expired, or already acknowledged tokens return False. There is no lease renewal yet;
work exceeding its lease can be delivered again. A real worker must acknowledge only
after its durable effect commits, and must make that effect duplicate-safe. Claim
tokens protect queue ownership, not database or external effects.

These scripts retain the same Redis persistence/no-eviction/resource-error limitations
as submission. This is at-least-once delivery, not exactly-once execution. Malformed
payloads remain recoverable but can poison the queue; dead-letter handling is future
work. Do not manipulate the queue keys outside these libraries.

Unit tests use fakeredis with Lua support. CI additionally runs the claim contract
against Redis 7.4. To run live checks locally, point the following variable at a
dedicated disposable database with no existing FlowLedger keys (never production):

```bash
FLOWLEDGER_TEST_REDIS_URL=redis://localhost:6379/15 python -m unittest discover -s tests -v
```

Live tests delete only their four fixed FlowLedger queue keys after each test. They
fail rather than clear pre-existing keys. Without the variable, live checks are
explicitly skipped. Duplicate-safe execution is next.

## Transactional outbox

After `python -m alembic upgrade head`, a new workflow can atomically retain its
initial command for later Redis delivery. This is currently a library interface;
the HTTP creation route does not schedule commands and no worker executes them yet.

```python
from sqlalchemy import create_engine
from flowledger.domain import Workflow
from flowledger.repository import SqlAlchemyRepository, build_session_factory
from flowledger.outbox import relay_pending

# Use your configured FLOWLEDGER_DATABASE_URL, with migrations applied.
sessions = build_session_factory(create_engine(database_url))
workflow = Workflow.create("synthetic outbox demo")
command = WorkerCommand(command_id=uuid4(), workflow_id=workflow.id, expected_version=0)
SqlAlchemyRepository(sessions).add(workflow, command)
relay_pending(sessions, queue, limit=100)
```

The command must reference the new workflow and its version and request a valid
transition. Creation does not execute that transition. The snapshot, initial event,
and outbox row commit together. The relay marks delivery only after Redis accepts
the command or recognizes an identical receipt. A failure leaves durable pending
intent; a crash after Redis acceptance is replayed using the same command ID.

Run the relay periodically in a future worker loop; it is not scheduled internally.
Each row uses a separate transaction and errors stop the batch. Concurrent relays
can submit the same command, relying on Redis deduplication, not distributed locks.
There is no strict global delivery ordering. Poison rows can block progress; retry
backoff, dead letters, retention cleanup, indexes for large backlogs, and duplicate-safe
execution remain future work. Set Redis connection/socket timeouts for bounded outages.
Delivered rows are retained for audit. Redis data loss after delivery marking still
requires reconciliation; this outbox does not guarantee exactly-once effects.

## Remaining stack

FastAPI, PostgreSQL, SQLAlchemy/Alembic, a Redis-backed worker, React/TypeScript,
Docker, GitHub Actions, and OpenTelemetry. Start as a modular application with a
worker; split services only for a demonstrated need.


