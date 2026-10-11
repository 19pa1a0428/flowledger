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
6. Add version checking and reproduce a concurrent transition conflict.
7. Document why the concurrency fix works and demonstrate it.

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
