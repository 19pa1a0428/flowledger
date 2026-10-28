# Learning roadmap

This is a sequence of acceptance criteria, not a promise that a calendar date
will produce a finished feature. Work progresses only when the preceding increment passes.

| Phase | Focus | Acceptance evidence |
| --- | --- | --- |
| 1: weeks 1–2 | FastAPI, PostgreSQL, SQLAlchemy and Alembic | Create/read/transition API; persisted history; rollback and concurrent-update tests |
| 2: weeks 3–4 | Worker jobs and reliability | Bounded backoff, duplicate submission protection, transactional outbox, crash-recovery tests |
| 3: weeks 5–6 | Security and auditability | Authentication, role checks, ownership enforcement, negative access tests |
| 4: weeks 7–8 | React and TypeScript | Workflow list/detail, pagination, failure/retry actions, loading/error/accessibility checks |
| 5: weeks 9–10 | Operations and performance | Trace a job across boundaries; documented reproducible load test and measured query improvement |
| 6: weeks 11–12 | Deployment and showcase | Docker Compose, CI quality gates, optional cloud deployment, recovery runbook and recorded demo |

## First increments

1. DONE: Domain transitions, retry budget, immutable event history and tests.
2. DONE: FastAPI endpoints backed by an explicitly temporary in-memory repository;
   validate inputs and map invalid transitions to useful HTTP errors.
3. DONE: API contract tests for creation, missing IDs, invalid transitions, and retry exhaustion.
4. DONE: PostgreSQL schema and reversible Alembic migration with workflow/event tables,
   ordered history, foreign keys, uniqueness, and invariant checks.
5. DONE: Replace temporary storage with a transactional SQLAlchemy repository;
   test restart persistence and rollback of snapshot/event writes.
6. DONE: Add version checking and reproduce a concurrent transition conflict.
7. DONE: Document why the concurrency fix works and demonstrate it.
8. DONE: Define the worker command envelope and add idempotent job submission backed by Redis.
9. DONE: live Redis integration checks and leased claim/acknowledgement with
   bounded recovery tests. Local emulator checks pass; live checks run in CI and
   require a disposable Redis URL locally. No executing worker or exactly-once claim.
10. DONE: Connect initial workflow creation intent to queue delivery through a
    transactional outbox, with rollback, restart, outage, and replay tests.
11. DONE: Database-only duplicate-safe execution and crash-before-ack recovery
    demonstrated. Durable terminal handling for validated missing/stale/invalid
    domain commands, with rejection replay and rollback tests. DONE: Redis-retained
    capped exponential retry scheduling, bounded due promotion, token-fenced retry
    and exhaustion storage for reported database errors and recovered lease expiry.
    Malformed envelopes and changed-payload command-ID conflicts are atomically
    quarantined with original payload, reason and Redis-server time.
12. DONE: Supervised bounded worker/maintenance loop with graceful shutdown,
    maintenance fairness and bounded batches. Read-only, paginated inspection exposes
    quarantined payloads and exhausted payloads with failure counts, including explicit
    corrupt-data handling; replay and deletion remain deliberately unsupported.
13. DONE: Opaque bearer authentication with fail-closed configuration, constant-time
    digest comparison, viewer/operator route authorization and negative API tests for
    missing, invalid and insufficient credentials.
14. DONE: Persist workflow ownership and event actors. Owner viewers can read their
    workflows, non-owner viewers receive non-enumerating 404 responses, operators retain
    cross-workflow access, and worker/API transitions have explicit audited actors.
15. DONE: Add timezone-aware credential expiry and explicit revocation semantics with
    deterministic clock and negative HTTP contract tests. Expired, revoked and unknown
    tokens share a generic authentication failure; configuration changes require restart.
16. DONE: Add a bounded, operator-only administrative audit query with newest-first,
    exclusive event-ID cursor pagination, actor/status filters and negative authorization
    and validation contracts.
17. NEXT: Add an owner-aware, cursor-paginated workflow list API as the backend contract
    for the first React/TypeScript workflow list view.

## Working cadence

Monday through Saturday: complete one coherent backlog increment, validate it,
and record what changed. Make 1–3 commits only when the work warrants that split.
Sunday: no automated work. Never create empty commits, backdate changes, change
author identity to disguise assistance, or modify unrelated repositories.

After each increment, include an explanation, a small hands-on exercise, and
an interview question. Failure to run a check must be reported explicitly.
Do not merge code with known failing required checks.

## Scope boundaries

No employer code, customer information, real recipient lists, or credentials.
No AI add-on until the deterministic workflow platform works and can be measured.



