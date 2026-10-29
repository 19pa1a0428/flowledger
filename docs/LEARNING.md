# Learning journal

## 2026-10-28 — Owner-aware workflow list contract

Assistance: AI-assisted list contract design, authorization/pagination tests and documentation.

Added `GET /workflows` as the backend contract for the first React view. Operators receive
all workflows, while viewers are restricted inside the database query to their immutable
owner subject. List records are lightweight summaries with current state, retry/version
metadata and UTC creation/update timestamps; event history remains on the detail endpoint.
An optional status filter uses the existing domain enum.

Results are ordered newest-first by `(created_at, id)`. The returned workflow ID anchors an
exclusive next-page predicate using the anchor's persisted creation time and ID, preventing
new inserts from shifting older pages. Pages are limited to 1–100 records and fetch one extra
row to determine whether `next_cursor` exists. A viewer cannot anchor pagination with another
owner's workflow; out-of-scope and nonexistent cursors share an invalid-cursor response.

Validation: Python 3.12 ran 112 tests locally: 91 passed and 21 live Redis contract tests
were explicitly skipped because no disposable Redis URL was configured. Six new tests cover
owner/status filtering, lightweight summaries, UTC timestamps, operator visibility, stable
pagination across a concurrent insert, cross-owner cursor denial and input bounds. Existing
domain, authentication, audit, migration, outbox, Redis reliability and worker tests passed.
No live PostgreSQL or Redis result is claimed locally. No schema migration was required.

Limits: cursor requests must retain the same filters. Creation ordering is stable, but status
changes or deletion between requests can change a filtered later page because this is not a
multi-request snapshot. There is no name search, total count or frontend client yet.

Exercise: create three synthetic workflows, fetch `GET /workflows?limit=2`, create a fourth,
then follow `next_cursor`. Verify the second page contains only the original oldest workflow.
Repeat with a viewer and confirm another owner's workflow never appears.

Interview question: Why enforce owner scope in the repository query instead of loading an
operator-sized page and filtering it in the API?

Answer outline: filtering after pagination can leak counts, return short or empty pages and
make cursors cross authorization boundaries. Applying policy in the query ensures every row
and pagination decision belongs to the caller's allowed dataset while reducing data access.

Next: scaffold React/TypeScript and implement typed workflow-list loading, empty, error and
accessible pagination states.

## 2026-10-27 — Operator audit query with stable pagination

Assistance: AI-assisted audit contract design, persistence/API tests and documentation.

Added a read-only `GET /audit/events` endpoint for operators. It returns persisted workflow
events newest-first with their event ID, workflow ID, position, status, timestamp, attempt,
actor and immutable workflow owner. Optional `subject` and `status` filters select the
audited actor and event status. Viewers receive `403`, and malformed, zero or oversized
pagination inputs receive `422` before reaching persistence.

Pagination uses the monotonically increasing event primary key as an exclusive cursor.
After a page ending at event N, the next query requests IDs below N. A deterministic test
inserts a newer event between page requests and proves it neither appears in the older page
nor causes duplicate entries. The query fetches one extra row to return `next_cursor` only
when more matching evidence exists. Pages are capped at 100 records.

Validation: Python 3.12 ran 106 tests locally: 85 passed and 21 live Redis contract tests
were explicitly skipped because no disposable Redis URL was configured. Five new tests
cover actor/status filtering, owner context, timezone restoration, stable pagination during
a concurrent insert, operator-only access and bounded input validation. Existing domain,
authentication, migration, outbox, Redis reliability and worker tests passed. No live
PostgreSQL or Redis result is claimed locally. No schema migration was required.

Limits: this query has no date range, text search, aggregate counts, export or database
snapshot spanning multiple requests. Event IDs provide stable forward paging while rows
remain immutable; workflow deletion would remove its events through the existing cascade.

Exercise: create two synthetic workflows, transition one to running, request audit events
with `limit=2`, then create another workflow before following `next_cursor`. Confirm the
second page contains only older event IDs and has no duplicate from the first page.

Interview question: Why use an exclusive event-ID cursor instead of page-number/offset
pagination for a live audit stream?

Answer outline: new rows inserted before an offset shift later pages, which can duplicate
or skip records. A unique ordered key anchors the next query below the last seen ID. It is
stable under new inserts and efficient with an index, though it is not a multi-page snapshot.

Next: add an owner-aware, cursor-paginated workflow list API for the first React view.

## 2026-10-26 — Deterministic credential expiry and revocation

Assistance: AI-assisted credential lifecycle design, deterministic tests and documentation.

Extended pre-provisioned opaque credentials with optional timezone-aware `expires_at` and
boolean `revoked` fields. Authentication accepts a credential strictly before its expiry;
the exact timestamp is already expired. An injected clock makes that boundary testable
without sleeps or timestamp manipulation. Revocation takes precedence, while credentials
without either field preserve the existing behavior.

Expired, revoked and unknown tokens deliberately use the same `401 Not authenticated`
response and Bearer challenge. This avoids revealing whether a presented secret used to be
valid. Configuration rejects naive timestamps, invalid timestamp types, non-boolean
revocation values and unknown fields. Existing programmatic token-to-principal mappings
remain compatible, while richer callers can construct an explicit `Credential`.

Validation: Python 3.12 ran 101 tests locally: 80 passed and 21 live Redis contract tests
were explicitly skipped because no disposable Redis URL was configured. Four new tests
cover pre-expiry access, exact-boundary expiry, continued expiry, explicit revocation,
active peer isolation, JSON parsing/validation and the shared negative HTTP contract.
Existing domain, persistence, migration, outbox, Redis reliability and worker tests passed.
No live PostgreSQL or Redis result is claimed locally.

Limits: expiry and revocation are static process configuration. Operators must update the
secret source and restart every API process; there is no issuance endpoint, refresh token,
dynamic revocation service, clock-skew allowance or persisted identity store.

Exercise: configure one synthetic token expiring one second after a fixed test clock and
one token with `revoked: true`. Advance the injected clock to the exact expiry boundary and
confirm both tokens receive the same `401` response as an unknown token.

Interview question: Why treat `now == expires_at` as expired and return the same response
for expired, revoked and unknown tokens?

Answer outline: a half-open validity interval gives an unambiguous boundary that services
can implement consistently. A generic response avoids turning authentication into a token
status oracle; internal observability can retain safe reason codes without exposing them.

Next: add a bounded, operator-only administrative audit query with stable pagination and
subject/status filters.

## 2026-10-24 — Workflow ownership and actor-aware audit

Assistance: AI-assisted ownership model, migration, access-control tests and documentation.

Persisted an immutable owner subject on every workflow and an actor subject on every audit
event. API creation assigns the authenticated operator as both owner and initial actor;
subsequent API transitions record the operator, while database worker execution records
the explicit `worker` actor. Domain and database constraints reject blank, padded or
overlong identities.

Owner viewers can read their workflow, but a different viewer receives the same `404` as
an unknown ID to reduce resource enumeration. Operators retain cross-workflow read and
transition access for operational support. The negative API contract proves an outsider
cannot read the workflow, while another operator can transition it and appears on the new
history event. Ownership and actors survive repository restart.

The reversible migration backfills existing workflows and events with `legacy-system`.
That label is deliberately honest: older rows did not capture identity, so attributing
them to a person would invent audit evidence. Temporary database defaults support the
backfill and are removed before new writes.

Validation: Python 3.12 ran 97 tests locally: 76 passed and 21 live Redis contract tests
were explicitly skipped because no Redis URL was configured. Updated domain, repository,
migration and API tests cover identity validation, durable owner/actor round trips,
reversible schema changes, owner reads, non-owner denial, operator override and actor
attribution. Existing outbox, Redis reliability, inspection and worker tests passed.
PostgreSQL offline SQL generation confirmed identity columns, constraints, legacy
backfill defaults and default removal. No live PostgreSQL or Redis result is claimed locally.

Limits: operators are intentionally global and ownership cannot yet be reassigned. Static
opaque credentials still have no expiry or revocation, and there is no administrative
audit query beyond history returned with an authorized workflow read.

Exercise: create a workflow as operator A, read it using a viewer token with A's subject,
then try a viewer token for B and confirm it receives `404`. Transition it as operator B
and verify the newest event's `actor_id` is B while `owner_id` remains A.

Interview question: Why return `404` instead of `403` when an authenticated non-owner asks
for a workflow by ID?

Answer outline: `403` confirms that the resource exists, enabling identifier enumeration.
Using the same `404` for missing and unauthorized resources hides that distinction. The
server must still log the authenticated denial internally so operators can investigate it.

Next: add deterministic credential expiry and revocation semantics.

## 2026-10-23 — Authenticated principals and role checks

Assistance: AI-assisted security boundary design, negative contract tests and documentation.

Added opaque bearer-token authentication around every workflow route. Configured secrets
map to a stable subject and either a viewer or operator role. Only token digests are retained
by the authenticator, every configured digest is compared for a request, and missing token
configuration fails closed. Operators can create, read and transition workflows; viewers
can read but receive `403` before mutation code runs.

The application factory accepts an injected authenticator for isolated tests and otherwise
parses `FLOWLEDGER_AUTH_TOKENS`. Configuration rejects short tokens, invalid roles, padded or
empty subjects, extra fields and malformed JSON. HTTP failures distinguish unauthenticated
requests (`401` with a Bearer challenge) from authenticated principals lacking permission
(`403`). A negative API test confirms denied viewer transitions leave the version unchanged.

Validation: Python 3.12 ran 97 tests locally: 76 passed and 21 live Redis contract tests
were explicitly skipped because no Redis URL was configured. Four new tests cover exact
token matching, fail-closed/configuration validation, missing and invalid credentials,
viewer reads, denied viewer writes and unchanged state. Existing workflow, persistence,
migration, outbox, Redis reliability, inspection and worker-loop tests passed. No live
PostgreSQL or Redis result is claimed locally.

Limits: this is pre-provisioned opaque-token authentication, not an identity provider.
Raw tokens still exist in the source environment and require TLS, secret management and
restart-based rotation. There is no login, issuance, expiry, revocation, persisted user,
per-workflow ownership or actor field in audit events yet.

Exercise: configure one viewer and one operator with synthetic 32-character tokens. Create
a workflow as the operator, read it as the viewer, then attempt a viewer transition and
confirm the response is `403` while a subsequent operator read still shows version zero.

Interview question: Why should an API return `401` for an invalid token but `403` for an
authenticated viewer attempting an operator action?

Answer outline: `401` means the server cannot establish an identity and includes an
authentication challenge. `403` means identity is established but policy denies the
operation. Keeping those cases distinct makes client behavior and security auditing clear
without revealing whether a protected resource exists.

Next: persist workflow ownership and enforce owner-or-operator access with actor-aware audit.

## 2026-10-22 — Read-only failure inspection

Assistance: AI-assisted inspection contract design, corruption tests and documentation.

Added bounded, oldest-first library inspection for quarantined and exhausted work.
Quarantine results expose the original payload, stable reason and Redis-server timestamp;
exhaustion results pair the raw payload with its retained failure count. Both interfaces
support offsets and limits without acknowledging, deleting, replaying or otherwise
changing queue state, and the records remain available through a fresh queue client.

Inspection validates retained data and raises `InspectionDataError` for corrupt JSON,
fields, text encoding, missing counters or invalid counter values. It intentionally keeps
the raw envelope rather than requiring today's `WorkerCommand` schema, so operators can
still investigate records written by another application version. Redis command errors
continue to propagate rather than being mistaken for an empty page.

Validation: Python 3.12 ran 93 tests locally: 72 passed and 21 live Redis contract tests
were explicitly skipped because no Redis URL was configured. Four new contract tests,
also inherited by the live Redis suite, cover bounded ordered quarantine pages, exhausted
failure counts after client restart, corrupt-data reporting without deletion, and page
validation. Existing API, migration, repository, outbox, retry, worker and loop tests
passed. No live Redis or PostgreSQL result is claimed locally.

Limits: list offsets are adequate for append-only operational inspection but are not an
opaque cursor or a transaction spanning the exhausted list and failure hash. There is no
authorized HTTP/operator API, replay, deletion, retention policy or archival process.

Exercise: force one synthetic command to exhaust with `RetryPolicy(max_failures=1)`,
construct a fresh `RedisClaimQueue`, call `list_exhausted()`, and verify both the original
JSON payload and failure count remain while the exhausted list length is unchanged.

Interview question: Why should an inspection interface return the raw retained payload
instead of only parsing it into the current command model?

Answer outline: poison records and older schema versions may not validate under current
code, but they are exactly the evidence an operator needs. Returning validated metadata
alongside the raw envelope preserves inspectability; replay should be a separate,
authorized workflow that creates new intent rather than silently mutating evidence.

Next: introduce authenticated principals and role-based API checks with negative tests.

## 2026-10-21 — Bounded worker supervision and graceful shutdown

Assistance: AI-assisted loop design, deterministic tests, and documentation.

Added `run_loop`, a supervisor-friendly library boundary around the existing single-poll
worker. Each cycle first recovers expired claims and promotes due retries in bounded
batches, then handles at most a configured number of commands. A full work batch starts
a new maintenance cycle immediately, while an idle queue waits on an Event-compatible
stop signal instead of busy-spinning. Batch, lease, and wait settings reject invalid or
unbounded values.

Shutdown is cooperative: the loop checks the signal between claims, so a request during
execution lets the current database transaction and queue acknowledgement finish but does
not claim another command. Unexpected database, Redis, and programming errors still
propagate. That makes failure visible to an external process supervisor rather than
silently retrying unknown failures inside this loop.

Validation: Python 3.12 ran 85 tests locally: 68 passed and 17 live Redis contract tests
were explicitly skipped because no Redis URL was configured. Four new deterministic tests
cover idle maintenance and interruptible waiting, work-batch fairness, shutdown after an
in-flight acknowledgement, and configuration bounds. Existing migration, API, repository,
queue, outbox, and worker tests also passed. No live Redis or PostgreSQL result is claimed.

Limits: this is a library loop, not a CLI or operating-system signal handler. The embedding
process must own the Event, signal registration, dependency construction, and restart
policy. There is still no lease renewal, scheduled outbox relay, or supported interface for
inspecting quarantined and exhausted payloads.

Exercise: configure `work_batch_size=2`, enqueue five synthetic commands, and instrument
`recover_expired` to count calls. Run the loop until the queue is idle and confirm
maintenance runs at least three times, then set the stop Event and verify the idle wait
returns promptly.

Interview question: Why should a worker check for shutdown between claims instead of
interrupting the current database operation?

Answer outline: interruption inside a transaction can leave its outcome uncertain, and
stopping before acknowledgement causes deliberate redelivery. Finishing the bounded
unit of work preserves commit-before-ack ordering; checking before the next claim limits
shutdown latency to one command. Idempotent receipts still protect ambiguous redelivery.

Next: add bounded, read-only inspection interfaces for quarantined and exhausted payloads.

## 2026-10-20 — Poison-command quarantine

Assistance: AI-assisted Redis quarantine design, worker handling, tests and docs.

Malformed JSON/schema envelopes and valid commands that reuse an executed ID with a
different payload now leave active delivery through an atomic, token-fenced quarantine
operation. Records retain the original payload, a stable reason and Redis-server time.
Malformed validation is handled after claim without losing the token; command-ID
conflicts are distinguished from transient database failures and are not retried.
Quarantine storage failures preserve pending or claimed work for later recovery.

Validation: Python 3.12 ran 81 tests: 64 passed and 17 live Redis checks skipped
because no local server/URL was available. Six new emulator/worker tests cover invalid
JSON and schema versions, structured records, stale tokens, unsupported reasons,
wrong quarantine key type, worker continuation, changed-payload conflicts and
quarantine outage recovery. The inherited queue contract will run the new checks
against Redis 7.4 in CI. Existing API, domain, repository, migration, retry and crash
checks passed. PostgreSQL offline migration SQL generation passed. Prior retry CI run
39349970714 passed on Python 3.11–3.13 with Redis 7.4. No new live PostgreSQL/Redis
or server-restart durability result is claimed locally.

Tradeoffs: quarantine is Redis-retained operational evidence, not the durable database
audit log. It has no automatic replay, inspection API, retention cleanup or operator
UI and depends on persistence/no eviction. Validation error text is intentionally not
stored; it can change across Pydantic releases, while the stable reason and original
payload are sufficient to revalidate. Corrected intent needs a new command ID.

Exercise: run `python -m unittest tests.test_claims.ClaimTests.test_malformed_payload_is_quarantined_atomically -v`.
Inspect the retained record and confirm recovery finds no expired claim for that input.

Interview question: Why quarantine poison work atomically with claim removal?
Answer outline: removing first can lose evidence if retaining the record fails; writing
first in a separate operation can duplicate quarantine records after a crash. One
token-fenced Lua operation makes the active delivery leave claim state exactly when
its diagnostic record is retained. This is atomic only inside Redis, not with the DB.

## 2026-10-19 — Redis-retained retry backoff and exhaustion

Assistance: AI-assisted queue scripts, worker error handling, tests and documentation.

Added configurable capped exponential delays, persisted Redis failure counters,
bounded due promotion and exhausted-payload retention. Explicit database operational/
integrity failures and expired-lease recovery use the same token-fenced scheduling
script logic. Successful acknowledgement clears counters; unknown errors still
propagate. Worker retries start after repository transaction exit rather than inside
a failed session. No real notifications or paid infrastructure were used.

Validation: Python 3.12 ran 72 tests: 58 passed, 14 live Redis checks skipped because
no local Redis server/URL was available. Eight new queue checks also run against Redis
7.4 in CI: capped server-time deadlines, bounded promotion, restart through a fresh
client, exhaustion, concurrent single-winner scheduling/promotion, expired budget,
stale ownership, bad counters/key types and policy bounds. Three new worker tests
cover database failure then success, retry-scheduling outage, and ambiguous commit
replay without duplicate effects. Existing API, domain, repository, migration and
rollback checks passed; PostgreSQL offline SQL generation passed. Prior CI run
39082355654 succeeded. No live PostgreSQL/Redis or server-restart durability result
is claimed; local queue tests used the Lua-capable emulator and retained state.

Tradeoffs: Redis retention/persistence and no eviction remain required. Lua resource
failures are not rollbacks. The budget counts reported errors and recovered expiry,
not workflow attempts; exhausted delivery may already have a committed DB receipt.
Inspect that receipt before deciding on corrected intent. Exhausted data accumulates,
there is no jitter or replay API, and maintenance must be called periodically even
when idle. All workers need the same policy and a coordinated rollout (old workers
ignore backoff). Malformed-payload quarantine and command-ID conflict handling remain
unfinished, so roadmap item 11 remains partial.

Exercise: run `python -m unittest tests.test_claims.ClaimTests.test_retry_exhaustion_and_restart -v`.
Inspect why a fresh queue object retains the failure budget and the second failure
leaves no pending command. The test injects due scores; production uses Redis time.

Interview question: Why store retry deadlines instead of sleeping inside a worker?
Answer outline: process-local timers disappear on restart and occupy workers. A
retained sorted set lets any maintenance caller promote due work in bounded batches.
Scheduling and claim release must be atomic, and execution still needs durable
idempotency because database commits and queue operations cannot share a transaction.

## 2026-10-17 — Durable terminal command rejection

Assistance: AI-assisted implementation, migration, fault-injection tests and docs.

Worker polling now records terminal domain rejection reasons before acknowledgement,
preventing unchanged stale, missing-workflow and invalid-transition commands from
cycling forever. A nested transaction rolls back the attempted transition while the
outer transaction retains the command payload and rejection. Identical redelivery
checks that receipt; changed payloads still conflict. Strict direct execution remains
backward compatible. Unknown exceptions and infrastructure failures remain recoverable.

Validation: Python 3.12 ran 53 tests: 47 passed, 6 live Redis checks skipped. Four new
worker tests cover all three rejection categories, restart/replay after commit-before-ack
failure, rejection commit rollback without acknowledgement, and unexpected error
propagation. SQLite migration upgrade/downgrade and PostgreSQL offline SQL generation
passed. Existing CI run 38987585375 succeeded. Full-suite TestClient checks required
the unrestricted runtime; tests used existing installed dependencies. No live
PostgreSQL or Redis validation is claimed.

Scope: validated command envelopes only; malformed payloads and command-ID conflicts
are not quarantined. Retry backoff, durable scheduling and exhaustion handling remain
unfinished, so roadmap item 11 remains partial. Receipts are indefinitely retained;
there is no rejection inspection API or automatic retry. A corrected command requires
a new ID and current version; the rejected command's outcome is permanent.

Exercise: run `python -m unittest tests.test_worker.WorkerTests.test_rejected_receipt_survives_restart_and_crash_before_ack -v`.
Inspect why the recovered command is acknowledged without invoking `_transition` again.

Interview question: Why commit a rejection before acknowledging the queue claim?
Answer outline: acknowledging first can lose the diagnostic outcome after a crash.
Committing first allows safe redelivery: the durable receipt preserves the decision
and prevents repeated processing. Database outages must not be recorded as permanent
domain failures.

## 2026-10-16 — Duplicate-safe database command execution

Assistance: AI-assisted code, migration, fault-injection tests and documentation.

Added durable command receipts atomically with workflow transitions and history.
The bounded run_once worker poll acknowledges only after database commit. Identical
replays do not reapply the transition; changed payloads conflict. Receipt uniqueness
arbitrates concurrent duplicates; a rejected transaction must be retried afresh.

Validation: Python 3.12 ran 49 tests: 43 passed and 6 live Redis checks skipped.
Five new tests cover restart replay, changed payload rejection, concurrent duplicates,
stale-command rollback, commit rollback, and a crash after commit before acknowledgement.
Migration upgrade/downgrade and PostgreSQL offline SQL generation passed. No live
PostgreSQL or live Redis execution result is claimed. Existing CI run 38831496778
completed successfully. FastAPI TestClient required the unrestricted runtime.

Scope: database-only transition effects, not arbitrary external actions or an
automatic job handler. Errors leave claims for recovery. Backoff and terminal
handling remain unfinished; roadmap item 11 is deliberately partial.

Exercise: run `python -m unittest tests.test_worker.WorkerTests.test_crash_after_commit_before_ack_is_safe -v`.
Inspect the two events after redelivery and locate the durable receipt.

Interview question: Why must the execution receipt and effect share a transaction?
Answer outline: a receipt committed first could suppress an effect that never ran;
an effect committed first could run twice after a crash. Atomic commit avoids both
windows for effects inside this database, not for external network calls.

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


