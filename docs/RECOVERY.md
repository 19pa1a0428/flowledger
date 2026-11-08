# Local recovery runbook

This runbook covers the synthetic Docker Compose environment, not a production
backup policy. PostgreSQL is the source of truth for workflows, history, receipts
and outbox delivery. Redis holds delivery state and append-only persistence; it is
not a substitute for the database.

## Start and verify

```bash
cp .env.example .env
docker compose up --build --detach --wait
docker compose ps
curl --fail http://127.0.0.1:8000/health/ready
```

`migrate` must exit successfully before the API, relay and worker start. The API is
ready only after a database round trip. Inspect a failed startup with
`docker compose logs migrate api relay worker`.

## Back up and restore PostgreSQL

Stop writers before a local logical backup so the exercise has one clear recovery
point, then dump the database to a file outside the container:

```bash
docker compose stop api relay worker
docker compose exec -T postgres pg_dump \
  --username flowledger --dbname flowledger --clean --if-exists > flowledger-backup.sql
docker compose start api relay worker
```

Treat the dump as sensitive if non-synthetic data is ever introduced. To rehearse a
restore, stop writers, replace the local database volume, start PostgreSQL, and load
the dump before restarting application processes:

```bash
docker compose stop api relay worker
docker compose down
docker volume rm flowledger_postgres-data
docker compose up --detach postgres
docker compose exec -T postgres pg_isready --username flowledger --dbname flowledger
docker compose exec -T postgres psql --username flowledger --dbname flowledger < flowledger-backup.sql
docker compose up --detach --wait
```

The explicit volume removal is destructive and is appropriate only for a disposable
local recovery drill after verifying the dump exists. Volume names can differ when
Compose is given another project name; confirm with `docker volume ls` first.

## Recover delivery

After an ordinary restart, keep both named volumes and run `docker compose up --detach
--wait`. The relay safely resubmits pending outbox rows by stable command ID. The worker
recovers expired claims after their lease and durable PostgreSQL command receipts prevent
a completed database transition from being applied twice.

If Redis data is lost, PostgreSQL remains authoritative. Restore the matching Redis volume/AOF
first when possible. If that is unavailable, stop the relay and worker and run the bounded,
read-only reconciliation report from a one-off application container:

```bash
docker compose stop relay worker
docker compose run --rm --no-deps relay python -m scripts.reconcile_outbox
```

The report scans delivered commands that have no durable database execution receipt. It lists
missing, present and conflicting Redis submission receipts without changing either store. Review
the command IDs and investigate every conflict. To resubmit only missing stable commands:

```bash
docker compose run --rm --no-deps relay \
  python -m scripts.reconcile_outbox --requeue-missing
docker compose start relay worker
```

Repair is explicit and bounded to 100 rows by default (`--limit` accepts 1-1000). A concurrent
reconciler converges through the atomic Redis receipt check. Rows with database execution receipts
are excluded because their effect or terminal rejection already committed; requeueing them would
be needless. Do not edit outbox delivery timestamps or Redis keys by hand.

This closes the missing-submission-receipt path, not every Redis disaster case. It does not prove
that an existing receipt still has a pending/delayed/claimed payload, reconstruct retry counters,
quarantine or exhaustion state, or retain an administrative audit record beyond existing database
and process logs. A production design still needs tested Redis backups, retention and monitoring.

## Disposable reset

For synthetic local data only, `docker compose down --volumes` removes both data volumes.
The next `docker compose up --build --detach --wait` creates an empty environment and runs
all migrations. This is a reset, not recovery.

## Automated disposable restore drill

With the synthetic stack running, the following command proves that a custom-format PostgreSQL
backup can recover a workflow snapshot and its two-event history into newly created volumes:

```bash
python -m scripts.restore_drill --confirm-disposable
```

The script creates synthetic data, waits for worker execution, captures `pg_dump`, runs
`docker compose down --volumes`, starts a fresh database, pipes the dump through `pg_restore`,
starts the full stack and checks the restored record through the API. The explicit confirmation
is a guardrail, not an interactive prompt. The command is destructive to the current Compose
project and must never be pointed at retained or production data. CI runs it in an isolated
runner and always removes the resulting volumes.

Passing this drill proves logical recovery for the exercised schema and synthetic record. It does
not establish a recovery-time objective, point-in-time recovery, encryption, off-site retention,
automated production backups or Redis delivery-state recovery.
