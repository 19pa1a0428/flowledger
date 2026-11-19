# Local alert response runbook

This runbook covers FlowLedger's optional local monitor. It is for disposable development and
portfolio demonstration environments using synthetic data. The monitor writes structured stdout;
it does not page, email, post messages, change readiness or mutate workflow/queue state.

## Start and inspect

```bash
docker compose --profile operations up --detach monitor
docker compose --profile operations logs --follow monitor
docker compose exec api python -m flowledger.operations
```

The health snapshot is the first corroborating check. Do not infer recovery from a missing monitor
sample. PostgreSQL and Redis are sampled sequentially rather than in one cross-store transaction.
Each record must contain `schema_version: 1` and validate against
`schemas/monitor-event-v1.schema.json`. Treat an absent or unsupported version as unreadable evidence;
do not guess its shape or reinterpret it as a healthy sample.

For bounded offline review, export raw JSONL with `docker compose --profile operations logs --no-color
--no-log-prefix monitor > monitor.jsonl`, then run `python -m flowledger.monitor_log monitor.jsonl`.
The summary counts transitions and stop reasons without printing error types or service identifiers.
Any rejected record makes the command return status 2; investigate the source rather than dropping the
record and treating the remaining file as complete evidence. The inspector treats each `stopped`
record as an explicit run boundary. Within a run it requires strictly increasing observations, legal
alert transitions and counters that agree with the preceding events; it rejects a file that ends with
an unfinished run. Concatenated runs may restart their observation clock and alert state only after a
`stopped` record.

| Event | Meaning | First check |
| --- | --- | --- |
| `sample` with `transition: fired` | A configured signal breached for its full activation streak | Confirm the latest payload-free snapshot and process logs |
| `sample` with `transition: recovered` | A firing signal was healthy for its full recovery streak | Confirm backlog/terminal counts remain stable for another sample |
| `sample_error` | PostgreSQL, Redis, schema, timestamp or exact-count sampling failed | Check dependencies and `error_type`; absence of data is not recovery |
| `stopped: consecutive_sample_errors` | The monitor exhausted its bounded error budget | Inspect the dependency before restarting; readiness may still be healthy |
| `CappedTerminalCounts` | Quarantine/exhaustion exceeded the exact count limit | Increase the local count limit only after checking memory/query impact |

## Oldest-outbox alert

1. Confirm API readiness separately; a backlog alert does not mean the API is unready.
2. Inspect `docker compose logs relay postgres redis` for a relay or dependency failure.
3. Run the payload-free health snapshot and note pending count plus oldest age.
4. If Redis receipts may be missing, run `python scripts/reconcile_outbox.py` in its default dry-run
   mode first. Use the explicit repair flag only after reviewing its report and recovery runbook.
5. Verify recovery through consecutive monitor samples. Do not delete outbox rows to clear an alert.

## Terminal-queue growth alert

1. Confirm whether `quarantined` or `exhausted` is growing with the health snapshot.
2. Use the bounded operator inspection APIs to review retained synthetic records and fixed reasons.
3. Correct the producer, schema or dependency problem before considering replay. FlowLedger does not
   provide automatic replay/deletion for these queues.
4. Preserve retained evidence until the cause is understood. Do not manipulate queue keys merely to
   force a green monitor state.

## Disposable integration proof

With the normal Compose stack running, execute:

```bash
python scripts/monitor_smoke.py
```

The smoke starts the profile with one-second sampling, adds two fixed synthetic markers to the Redis
quarantine list, and requires the terminal-growth alert to fire and recover. It then uses a count
limit of one and requires a fail-closed `CappedTerminalCounts` exit. Both phases remove only their
exact markers in `finally`; CI also destroys its disposable volumes afterward. Do not run the smoke
against retained or shared Redis data. A force-killed host process may bypass cleanup, in which case
remove only the two documented `flowledger-monitor-smoke-*` markers after verifying their identity.

## Boundary

These signals are local operational evidence, not production service objectives or an on-call
system. Thresholds require environment-specific review. Stdout and policy state are ephemeral, a
restart resets the terminal baseline, and no delivery acknowledgement exists because notifications
are deliberately out of scope.
