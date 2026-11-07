# Synthetic portfolio demo

This walkthrough demonstrates FlowLedger's implemented reliability boundaries with local,
synthetic data. It does not send notifications, use employer data or provision cloud resources.

## Prepare the stack

Install Docker with Compose, then from the repository root:

```bash
cp .env.example .env
docker compose up --build --detach --wait
python -m scripts.demo_walkthrough
```

The script uses the local-only operator credential from `.env.example`. Set
`FLOWLEDGER_DEMO_URL` and `FLOWLEDGER_DEMO_TOKEN` to exercise another explicitly authorized
environment; the token is read from the environment and is never printed.

## Five-minute narrative

1. **Modular boundary:** one application image runs migrations, FastAPI, the transactional-outbox
   relay and the worker as separate processes. PostgreSQL is authoritative; Redis coordinates
   retained delivery. This keeps domain, persistence and job modules separate without inventing
   independently deployed services.
2. **Durable submission:** the script creates a pending workflow. The API commits the snapshot,
   first audit event and outbox command together. The relay delivers the stable command to Redis,
   and the worker advances the workflow to `running`.
3. **Audited failure and retry:** the script marks the first attempt failed, returns it to pending,
   starts attempt two and completes it successfully. The final six-event history is checked rather
   than merely printed.
4. **Concurrency safety:** after failure, the script deliberately submits a transition using the
   older running version. The expected `409` proves a stale operator cannot overwrite newer state.
5. **Operational evidence:** point to correlated trace-context tests, the measured PostgreSQL
   workflow-list index, container/SBOM gates and the destructive PostgreSQL restore drill. These
   are reproducible engineering checks, not production scale or uptime claims.

Expected final output has this shape; the UUID is generated at runtime:

```text
Workflow: <synthetic UUID>
History: pending -> running -> failed -> pending -> running -> succeeded
Demo passed: succeeded, 2 attempts, version 5; stale write rejected
```

The React client can be shown separately with `cd frontend && npm run dev`. Use the same synthetic
token to inspect the generated workflow, event chronology and state-aware operator controls.

## What this proves—and does not prove

The runner checks the live API, outbox/Redis/worker delivery, optimistic conflict response, retry
budget and persisted history through public HTTP contracts. Unit and CI tests cover outage and
crash windows that would be awkward to stage during a short demo.

It does not establish production availability, throughput, cloud security, multi-host migration
coordination, Redis disaster reconstruction or external-message exactly-once delivery. The demo
uses one local Compose deployment and synthetic database-only effects. See the deployment decision
record and recovery runbook for those boundaries.
