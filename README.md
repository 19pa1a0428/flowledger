# FlowLedger

A portfolio project for reliable workflow execution and notifications.

The planned system lets operators create workflows, schedule synthetic notifications,
inspect failures, and retry work without duplicating effects. The focus is backend
correctness, operational visibility, and clear engineering tradeoffs.

## Current implementation

- Immutable workflow snapshots and UTC event history.
- Explicit state transitions and bounded retry attempts.
- Tests covering every status pair and retry exhaustion.
- FastAPI create/read/transition endpoints and API contract tests.
- A GitHub Actions test matrix for Python 3.11–3.13.

This is a local API prototype. Persistence, authentication, workers, notifications,
and the UI are not implemented yet. Storage is temporary, lost on restart, and
separate in each server process. Use one process and synthetic data only. In-memory history is
not a durable audit log, and immutable objects do not provide concurrency control.

## Run the tests

Requires Python 3.11 or newer. Install the API and test dependencies:

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

## Design

A workflow starts pending, then runs and either succeeds or fails.
Failed work may return to pending while its execution budget remains.
Pending or failed work may be cancelled. Success and cancellation are terminal.
`max_attempts` includes the initial execution, rather than counting only retries.

See [the roadmap](docs/ROADMAP.md), [the first design decision](docs/adr/0001-domain-boundary.md),
and [the learning journal](docs/LEARNING.md).

## Development approach

Small, testable increments with concrete commit messages. AI-assisted changes are
identified in the learning journal; entries describe verified work, not personal
experiences or results that did not happen. Only synthetic data belongs here.
There is no scheduled empty-commit script.

## Planned stack

FastAPI, PostgreSQL, SQLAlchemy/Alembic, a background worker with Redis,
React/TypeScript, Docker, GitHub Actions, and OpenTelemetry.
Start as a modular application with a worker; split services only for a demonstrated need.


## Try the API

```bash
uvicorn flowledger.api:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/docs for interactive examples.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/workflows` | Create with `name` and optional `max_attempts`; 201 plus Location |
| GET | `/workflows/{id}` | Read snapshot and event history |
| POST | `/workflows/{id}/transitions` | Transition with `status`; return updated snapshot |

Names are trimmed, nonblank, and at most 200 characters. Attempts must be an
integer from 1 to 100; booleans and numeric strings are rejected. Unknown fields
are rejected. Malformed input returns 422, missing IDs 404, and lifecycle or
retry conflicts 409. Rejected transitions leave the stored snapshot unchanged.
A process-local lock serializes transitions only within one application instance;
it provides no cross-process coordination. Endpoints do not execute jobs.
