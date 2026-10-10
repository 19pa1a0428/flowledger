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
- PostgreSQL-native UUID columns, ordered event history, foreign keys, and database checks.
- A GitHub Actions test matrix for Python 3.11–3.13.

The API still uses temporary in-memory storage. It loses data on restart and each
server process has separate state. The relational schema is now defined, but wiring
the API to transactional database sessions is the next increment.

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

For a local PostgreSQL instance, override the development URL in `alembic.ini`:

```bash
python -m alembic -x unused=unused upgrade head
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
uvicorn flowledger.api:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/docs. The current routes are `POST /workflows`,
`GET /workflows/{id}`, and `POST /workflows/{id}/transitions`.

## Planned stack

FastAPI, PostgreSQL, SQLAlchemy/Alembic, a Redis-backed worker, React/TypeScript,
Docker, GitHub Actions, and OpenTelemetry. Start as a modular application with a
worker; split services only for a demonstrated need.
