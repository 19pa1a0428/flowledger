# Learning journal

## 2026-10-07 — Workflow lifecycle foundation

Assistance: AI-assisted implementation and test creation.

Implemented immutable workflow snapshots, UTC events, explicit lifecycle
transitions, and a total-attempt retry budget. Domain tests exercise every
status pair, invalid configuration, retry exhaustion, and original snapshot preservation.

Tradeoff: returning new snapshots is simple to reason about, but persistence
and concurrent workers still require transactional version checks.
No API, database, or worker behavior is claimed by these tests.

Exercise: run the suite, change max_attempts to 1 in a local example, and explain
why a failed workflow cannot return to pending.

Interview question: Why does an immutable domain model fail to prevent duplicate
job execution across two worker processes?

Answer outline: each process can hold a valid but stale snapshot; coordinate
through database transactions/version checks and protect external side effects
with idempotency keys.

Validation: `python -m unittest discover -s tests -v` passed all 5 tests locally.
The Python-version matrix is configured in CI but has not yet been verified remotely.

## 2026-10-08 — HTTP boundary and temporary repository

Assistance: AI-assisted implementation, contract tests, and documentation.

Added create/read/transition routes, typed response schemas, Location on creation,
strict bounded configuration, and 422/404/409 error mappings. The domain remains
independent of HTTP. A temporary repository serializes transitions within one
process; application instances remain isolated and restart loses all data.
These limits are documented rather than presented as durable persistence.

Validation: Python 3.12 locally passed all 12 unittest tests (5 domain, 7 API).
API tests cover creation/read, input rejection, missing/malformed IDs, unchanged
history after conflict, retry exhaustion, terminal success and storage isolation.
CI now installs the test extra before running the Python 3.11–3.13 matrix.
The installed Starlette emits a TestClient/httpx deprecation warning; tests pass,
and a future dependency update should migrate the test transport.

Exercise: start the API, create a workflow with max_attempts=1, transition it to
running then failed, and attempt pending. Inspect the 409 response and unchanged
event history using GET. Restart the server and observe that the ID returns 404.

Interview question: Why return 409 for a valid status that cannot be applied?
Answer outline: the request is structurally valid but conflicts with current
resource state; 422 describes invalid input, while 404 describes a missing
resource. Keep lifecycle policy in the domain and translate it at the HTTP edge.

Next: PostgreSQL workflow/event schema and an Alembic migration.
