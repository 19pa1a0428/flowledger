# Learning journal

## 2026-10-15 — Transactional command outbox

Assistance: AI-assisted implementation, fault-injection tests, and documentation.

Added a reversible command_outbox migration and optional initial command on repository
creation. Workflow snapshot, initial event, and intent commit atomically. A bounded relay
submits stable command IDs to Redis and marks delivery only after acceptance or identical
replay. It does not execute transitions and is not yet exposed as an HTTP scheduling route.

Validation: Python 3.12 ran 44 tests: 38 passed, 6 live Redis checks skipped (no local
server). Seven new tests cover durable intent after engine restart, bounded batches,
queue failure, crash after acceptance, full transaction rollback after flush, receipt
conflicts, invalid intent and batch limits. PostgreSQL offline migration SQL generated
successfully; SQLite migration upgrade/downgrade passed. No live PostgreSQL result is
claimed. The restricted runtime stalled in TestClient; the full suite passed in the
unrestricted runtime. Previous increment CI run 38660028320 completed successfully.

Tradeoffs: concurrent relays can replay submissions; Redis receipts suppress duplicate
enqueue only while retained. Poison rows stop a batch, delivered rows accumulate, and
Redis loss after delivery marking requires reconciliation. No exactly-once execution,
backoff, cleanup, or background relay loop is claimed.

Exercise: run `python -m unittest tests.test_outbox.OutboxTests.test_crash_after_redis_acceptance_replays_without_second_enqueue -v`.
Inspect why the retry marks the pending row delivered while the queue length stays one.

Interview question: Why mark outbox delivery after queue submission rather than before?

Answer outline: marking first can lose work if the process crashes before submission.
Submitting first can cause replay after a crash, which a stable command ID and retained
receipt make safe at the queue boundary. Execution still needs its own idempotency design.

Next: bounded retry backoff and duplicate-safe worker execution.

## 2026-10-07 — Workflow lifecycle foundation

Assistance: AI-assisted implementation and test creation.

Implemented immutable workflow snapshots, UTC events, explicit lifecycle transitions,
and a total-attempt retry budget. Validation passed all 5 domain tests locally.

## 2026-10-08 — HTTP boundary and temporary repository

Assistance: AI-assisted implementation, contract tests, and documentation.

Added create/read/transition routes, typed response schemas, Location on creation,
strict bounded configuration, and 422/404/409 error mappings. The repository remains
temporary and process-local. Validation passed all 12 tests locally.

## 2026-10-09 — Relational schema and first migration

Assistance: AI-assisted schema design, migration tests, and documentation.

Added SQLAlchemy 2 models for workflow snapshots and ordered event history, plus the
first reversible Alembic migration. UUID workflow identifiers map to native PostgreSQL
UUID columns. Foreign keys cascade event deletion, `(workflow_id, position)` is unique,
and check constraints protect status values, attempt bounds, and event counters.

Tradeoff: status is stored as a constrained string instead of a PostgreSQL enum. That
makes future status additions a migration of a check constraint and keeps local SQLite
migration tests representative. The API intentionally remains in-memory until the next
increment can introduce database sessions, atomic writes, and rollback tests together.

Validation: Python 3.12 passed all 14 tests locally (5 domain, 7 API, 2 migration).
The migration upgraded and downgraded a fresh temporary database. Alembic offline mode
also generated PostgreSQL DDL containing UUID columns and named constraints. A live
PostgreSQL server was not provisioned, so no live-engine integration result is claimed.

Exercise: run `python -m alembic upgrade head --sql`, find the unique event-position
constraint, and explain what bad history it prevents. Then run the test suite and inspect
the temporary migration test.

Interview question: Why store an explicit event position when every event has a timestamp?

Answer outline: timestamps can collide and database result order is undefined without
an ordering key. A per-workflow sequence gives deterministic reconstruction; a unique
constraint rejects duplicate positions. Concurrency still needs a transaction/version
strategy, which is the next increment.

Next: replace temporary API storage with transactional persistence and test restart
persistence plus rollback behavior.

## 2026-10-10 — Transactional repository and restart durability

Assistance: AI-assisted implementation, persistence tests, and documentation.

Replaced the process-local dictionary with a SQLAlchemy repository. Creating a workflow
stores its snapshot and initial event together. Each transition loads ordered history,
applies the existing domain rules, then updates the snapshot and appends exactly one event
inside the same session transaction. The API accepts an injected repository for tests and
otherwise reads `FLOWLEDGER_DATABASE_URL`, defaulting to the local PostgreSQL URL used by
Alembic.

Two persistence tests provide the new acceptance evidence. One closes the original engine,
opens a new engine and repository against the same file, and verifies the workflow and
ordered events survived. The other raises a synthetic error during commit and verifies a
new session still sees the original snapshot and only the original event. API contract
tests now run through SQLAlchemy rather than a separate in-memory implementation.

Tradeoff: this increment deliberately does not claim safe concurrent writes. Two sessions
can still load the same state before either commits. A version column and compare-on-update
behavior belong in the next increment, where a repeatable conflict test can prove the fix.

Validation: Python 3.12 passed all 16 tests locally (5 domain, 7 API, 2 repository,
2 migration). SQLite provides fast transaction and restart tests; a live PostgreSQL server
was not provisioned, so no live PostgreSQL result is claimed.

Exercise: create a workflow through the API, stop the server, start it again with the same
`FLOWLEDGER_DATABASE_URL`, and retrieve the workflow by ID. Then inspect both database rows
to confirm the snapshot and position-zero event agree.

Interview question: Why write the current snapshot and audit event in one transaction?

Answer outline: they represent one state change. If only one write commits, reads either
show a state with no explanation or a history event that disagrees with current state.
A single transaction makes both visible together or rolls both back. This guarantees
atomicity, but does not yet prevent two concurrent transactions from overwriting each other.

Next: add explicit version checking and a deterministic concurrent-transition conflict test.

## 2026-10-12 — Optimistic concurrency control

Assistance: AI-assisted implementation, migration design, conflict tests, and documentation.

Added a nonnegative `version` to workflow snapshots and API responses. A transition must
send the version it previously read. The repository validates that precondition and then
updates the snapshot with `WHERE id = ... AND version = ...`. Exactly one writer can advance
a given version; a zero-row update becomes `ConcurrencyConflict`. The event is inserted only
after that comparison succeeds and remains in the same transaction as the snapshot update.

Repository and HTTP tests reproduce two writers acting on version zero. The first moves the
workflow to running and version one. The stale writer receives a conflict, while the stored
snapshot and two-event history remain unchanged. This proves both the user-facing contract
and the persistence invariant rather than relying only on an ORM versioning feature.

Tradeoff: clients now carry `expected_version`, adding one field and a conflict-recovery
path. In return, conflicts are explicit instead of silently losing an update. Pessimistic
row locks were not chosen because they hold database resources while work is in flight and
still do not communicate a stale client decision as clearly.

Validation: Python 3.12 passed all 18 tests locally (5 domain, 8 API, 3 repository,
2 migration). The Alembic chain upgraded and downgraded a temporary SQLite database. A live
PostgreSQL concurrency test was not run, so no live-engine result is claimed.

Exercise: create a workflow, save its version zero response, and submit a running transition
twice with `expected_version: 0`. Confirm the first response is version one, the second is
`409`, and a fresh GET contains only the pending and running events.

Interview question: How does a conditional update prevent lost updates without a row lock?

Answer outline: every writer compares the version it read in the UPDATE predicate. The first
matching transaction increments the version. Later stale updates match zero rows and roll
back, including any event insert. This is optimistic concurrency: it assumes conflicts are
uncommon but detects them atomically when they occur.

Next: begin the reliability phase with a worker command envelope and idempotent submission.

## 2026-10-13 — Command envelope and Redis submission

Assistance: AI-assisted implementation, tests, and documentation.

Added a frozen, versioned command schema and Redis submission library. One Lua script
checks a retained receipt, rejects a changed payload for the same command ID, and enqueues
new commands. Both keys use the same cluster hash tag. Invalid key types are detected
before writes because Lua errors do not roll back preceding mutations.

Validation: Python 3.12 passed all 25 tests locally. Seven new tests cover schema rejection,
exact replay through a fresh client, payload conflicts, 24 concurrent submissions with
one winner, distinct command ordering, invalid queue type, and connection failure.
Redis checks used fakeredis 2.31.1 with Lua support, not a live server. Existing API,
repository, domain, and migration tests also passed. A sandbox test-client run stalled;
the complete suite passed in the unrestricted runtime after installing declared dependencies.

Limits: no worker execution or HTTP submission route yet. Receipts grow indefinitely;
deduplication depends on Redis retention. Memory errors can leave partial Lua writes,
and PostgreSQL/Redis delivery is not atomic. No live Redis crash/durability result is claimed.

Exercise: run the README's synthetic submission example against local Redis. Re-submit
the command with the same ID and expected_version changed to 1; verify SubmissionConflict
and that the pending list still contains one entry.

Interview question: Why is SET NX followed by a separate queue append unsafe?

Answer outline: a crash between the two operations leaves a receipt without queued work,
so retries can be incorrectly suppressed. A Lua script prevents concurrent interleaving
of these steps, but is not a rollback transaction or an exactly-once execution guarantee.
Durability, resource failures, and database-to-queue delivery need separate designs.

Next: live Redis integration and claim/acknowledgement recovery, followed by the outbox.

## 2026-10-14 — Leased queue claims and recovery

Assistance: AI-assisted implementation, fault-injection tests, and documentation.

Added atomic claim, acknowledgement, and bounded expired-claim recovery scripts.
Each claim has a fresh UUID token and deadline calculated with Redis server time.
Recovery returns expired payloads to pending; stale tokens cannot acknowledge a new
claim. Key types are checked before mutation. No workflow effects are executed yet.

Validation: Python 3.12 ran 37 tests locally: 31 passed and 6 live Redis contract
checks were explicitly skipped because no Redis server was available. The six new
emulator tests cover empty queues, acknowledgement/replay, concurrent claim ownership,
crash recovery with a fresh queue object, bounded recovery, invalid inputs, and key
type safety. Expiry is injected into the deadline set to avoid timing-dependent sleeps.
CI now provisions Redis 7.4 and runs the same six checks against the real engine;
its result must be inspected separately, not inferred from emulator success.

Tradeoffs: this provides at-least-once delivery under retained Redis state, not
exactly-once effects. A worker can finish after its lease expires, so later execution
must be idempotent. There is no heartbeat/lease extension, dead-letter policy, or
database/Redis transaction yet. Redis resource errors can still leave partial writes.

Exercise: submit a synthetic command, claim it with a short lease, and stop before
acknowledging. After expiry call recover_expired, claim again, and verify that the
old token is rejected while the new token can acknowledge the same command.

Interview question: Why use a fresh claim token instead of acknowledging by command ID?

Answer outline: the same command may be redelivered after a worker stalls. If both
workers acknowledge by command ID, the old worker can accidentally remove the newer
worker's claim. A per-delivery token fences queue acknowledgement; duplicate-safe
database effects still need a separate transaction/idempotency design.

Next: transactional outbox delivery, followed by bounded retries and duplicate-safe execution.


