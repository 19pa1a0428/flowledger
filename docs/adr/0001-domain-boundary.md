# ADR 0001: Keep workflow rules independent of infrastructure

Status: Accepted

## Context

HTTP requests and background workers must enforce the same lifecycle rules.
Putting those rules in routes would duplicate them as other entry points arrive.

## Decision

Use an immutable Python domain model with explicit transitions. Keep database
sessions, queue clients, and HTTP response types outside this module.

## Consequences

Rules can be tested without services. Failed transitions leave prior snapshots
untouched. Persistence must later use optimistic concurrency or transactions;
the model alone cannot prevent two workers from executing the same workflow.

A failed attempt returns to pending only when attempts remain. Cancellation of
running work is deferred until cooperative worker cancellation is designed.
The current event history is process-local, not durable or tamper-resistant.
