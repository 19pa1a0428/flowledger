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

## Authentication and roles

Every workflow route requires an opaque bearer token. Operators may create, read and
transition any workflow; viewers may only read workflows they own. Configure credentials
as a JSON list in `FLOWLEDGER_AUTH_TOKENS`. Missing configuration is fail-closed: the
application starts but every protected request returns `401`.

```bash
export FLOWLEDGER_AUTH_TOKENS='[
  {"token":"replace-with-a-random-secret-of-at-least-32-characters","subject":"local-operator","role":"operator","expires_at":"2026-11-01T00:00:00Z","revoked":false}
]'
```

| Route | Viewer | Operator |
| --- | --- | --- |
| `POST /workflows` | 403 | Allowed |
| `GET /workflows/{id}` | Owner only | Allowed |
| `POST /workflows/{id}/transitions` | 403 | Allowed |

Tokens must contain 32–4096 characters. FlowLedger stores only SHA-256 digests in the
authenticator and uses constant-time digest comparison, but the source environment still
contains the raw secrets. Generate strong random values with a secret manager, use TLS,
avoid committing or logging them, and restart after rotation. `expires_at` is optional,
must be an ISO 8601 timestamp with a timezone, and expires the token at the exact boundary
(`now >= expires_at`); there is intentionally no clock-skew allowance. `revoked` is an
optional boolean that defaults to false. Expired, revoked and unknown tokens all receive
the same generic `401` response. Configuration is loaded at process startup, so changing
expiry or revocation requires a restart. This boundary has no login, token issuance,
dynamic revocation store, persisted users or ownership reassignment.

New workflows store the creating operator's subject as `owner_id`. Each history event
stores the subject that caused it as `actor_id`; database-only worker transitions use
the explicit actor `worker`. A non-owner viewer receives `404`, matching an unknown ID
so the API does not disclose another principal's workflow. Operators can inspect and
transition any workflow for operational support, but cannot reassign ownership. The
ownership migration marks existing snapshots and events as `legacy-system` because their
historical human actor cannot be reconstructed honestly.

## Try the API

```bash
python -m alembic upgrade head
uvicorn flowledger.api:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/docs. The current routes are `POST /workflows`,
`GET /workflows/{id}`, and `POST /workflows/{id}/transitions`.
Use the OpenAPI **Authorize** control or send `Authorization: Bearer <token>`.
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
worker_queue.promote_due(limit=100)      # Expired work is delayed, not immediately pending.
claim = worker_queue.claim(lease_ms=30_000)
if claim is not None:
    print(claim.command)  # Inspection only: no workflow effects executed here.
    assert worker_queue.acknowledge(claim.token)
```

Claiming atomically moves one pending payload into a token-indexed hash and lease
deadline set. Redis server time determines expiry and retry deadlines. Recovery moves
expired claims to delayed or exhausted storage in bounded batches; due promotion
returns delayed payloads to pending. Each new claim receives a new token. Acknowledgements for old,
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

Live tests delete only their eight fixed FlowLedger queue keys after each test. They
fail rather than clear pre-existing keys. Without the variable, live checks are
explicitly skipped. These checks cover retry scheduling as well as claims.

## Transactional outbox

After `python -m alembic upgrade head`, a new workflow can atomically retain its
initial command for later Redis delivery. This is currently a library interface;
the HTTP creation route does not schedule commands. Worker polling is a separate library call.

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
backoff for the relay itself, retention cleanup and indexes for large backlogs remain
future work. Worker execution has its own retry policy below. Set Redis
connection/socket timeouts for bounded outages.
Delivered rows are retained for audit. Redis data loss after delivery marking still
requires reconciliation; this outbox does not guarantee exactly-once effects.

## Remaining stack

## Database-only worker execution

Apply migrations before using `flowledger.worker.run_once(claims, repository)`.
It claims at most one command, commits a workflow transition and command receipt
in the same database transaction, then acknowledges Redis. An identical command
replay is a no-op even after restart or Redis receipt loss. Changed payloads for
an executed ID raise SubmissionConflict. Receipts are retained indefinitely.

Only database transitions are supported: this does not make external effects
exactly-once, execute a notification, or automatically finish a running workflow.
The caller must periodically recover expired claims. The worker retains terminal
domain rejections before acknowledging: `command_receipts.rejection` is
`workflow_missing`, `stale_version`, or `invalid_transition`; NULL means applied.
A savepoint rolls back the rejected transition while the outer transaction commits
the receipt. Replays of rejected commands do not reconsider them, even if workflow
state later changes. Inspect receipts through a database session; no inspection API
or automatic resubmission exists yet. Corrected intent needs a new command ID and
the current workflow version. Direct `repository.execute` retains its strict default
(exceptions and no receipt); worker polling opts into `reject_invalid=True`.

Database OperationalError and IntegrityError release the rolled-back claim into the
retry schedule. Unknown exceptions still propagate, leaving the claim for expiry
recovery. Malformed queue payloads and changed-payload command-ID conflicts are
atomically retained in quarantine and removed from active delivery. Redis errors
propagate. Apply the latest migration before deploying this worker.

## Bounded worker retries

`RedisClaimQueue(client, RetryPolicy(max_failures=3, base_delay_ms=1000,
max_delay_ms=60000))` configures reported database failures and recovered lease
expiries. Use the same policy on every worker/recovery caller. With the defaults,
the first failure waits 1 second, the second 2 seconds, and the third goes to
`flowledger:{jobs}:exhausted`. Delays are `min(cap, base * 2**(failures-1))`;
there is no jitter yet. The failure budget is separate from workflow `max_attempts`.

`retry(token)` atomically schedules the original payload, increments its Redis
failure count and removes the active claim, returning `scheduled`, `exhausted`, or
`stale`. Stale/expired tokens cannot consume another owner's budget. Recovery applies
the same policy to expired claims. Call `recover_expired()` and `promote_due()`
periodically, even when `run_once()` returns False; batches are limited to 1000.
`flowledger.worker.run_loop` provides the supervised loop. Every cycle runs bounded
expired-claim recovery and due promotion before handling at most one configured batch.
When the queue becomes idle it waits on a `threading.Event`-compatible stop signal, so
shutdown interrupts the wait without busy-spinning. A stop requested during execution
is honored after the current database effect and acknowledgement finish; the loop does
not abandon an in-flight transaction. Queue/Redis and unexpected repository errors
propagate for an external process supervisor to observe and restart.

```python
from threading import Event
from flowledger.worker import WorkerLoopConfig, run_loop

stop = Event()
stats = run_loop(claims, repository, stop, WorkerLoopConfig(
    work_batch_size=100,
    maintenance_batch_size=100,
    lease_ms=30_000,
    idle_wait_seconds=1.0,
))
```

The embedding process owns signal registration and calls `stop.set()` during shutdown.
There is no command-line worker entry point, lease renewal, automatic process restart,
or outbox relay scheduling yet. `WorkerLoopStats` reports completed loop activity when
shutdown is graceful. Successful acknowledgement clears the payload's failure counter;
no sleeping worker or in-memory timer owns a retry deadline.

Delayed payloads and counters survive client restart only while Redis state survives.
Use persistence and no eviction; Lua resource failures can still leave partial writes.
Exhausted payloads and their counters are retained indefinitely for inspection, not
automatically replayed. `list_exhausted(offset=0, limit=100)` returns an oldest-first,
bounded page with each raw payload and retained failure count.
Inspect `command_receipts` before manually creating new intent: an expired claim or
ambiguous commit response can exhaust the queue budget even if its database effect
already committed. Exhaustion is a delivery outcome, not proof that the workflow
transition failed. Database receipts remain the duplicate-effect safeguard.

Stop old workers before rollout: older recovery code requeues immediately and does
not honor the new retry budget. No Redis key migration is required for empty/new
retry state; existing claims begin with zero recorded failures.

## Command quarantine

Claims that fail `WorkerCommand` validation are moved to
`flowledger:{jobs}:quarantine` with reason `malformed_command`. Valid commands whose
ID already has a different durable execution payload are quarantined with reason
`command_id_conflict`. Each retained JSON record contains the original payload,
reason and Redis-server quarantine time in milliseconds. Validation details are not
stored because they may change between application versions; the original payload
can be revalidated during investigation.

Quarantine is token-fenced and atomic: only the active, unexpired owner can remove a
claim and append its record. Stale tokens cannot quarantine newer ownership. Key
types are checked before claiming, so a broken quarantine key leaves input pending;
an outage after claim keeps it recoverable. Quarantine clears the payload's retry
counter and is terminal. `list_quarantined(offset=0, limit=100)` returns an
oldest-first page containing raw payload, stable reason and quarantine timestamp.
Correct the cause and submit a new command ID rather than editing
retained Redis data. Quarantine depends on the same Redis persistence/no-eviction
assumptions as the queue and is not an audit substitute for database receipts.

## Failure inspection

```python
for item in worker_queue.list_quarantined(limit=50):
    print(item.reason, item.quarantined_at_ms, item.payload)

for item in worker_queue.list_exhausted(limit=50):
    print(item.failure_count, item.payload)
```

Inspection is read-only and limited to 1–1000 records per call. It does not acknowledge,
requeue, delete, or reinterpret payloads, and works after constructing a fresh queue
client because Redis owns the retained state. Invalid JSON, fields, encoding, or failure
counters raise `InspectionDataError` while leaving evidence untouched. Pages use list
offsets rather than opaque cursors; concurrent appends are safe for an oldest-first page,
but no cross-key snapshot is claimed. There is no HTTP/operator API, replay workflow,
authorization layer, or retention cleanup yet.

FastAPI, PostgreSQL, SQLAlchemy/Alembic, a Redis-backed worker, React/TypeScript,
Docker, GitHub Actions, and OpenTelemetry. Start as a modular application with a
worker; split services only for a demonstrated need.



