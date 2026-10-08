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
