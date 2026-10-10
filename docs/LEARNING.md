# Learning journal

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
