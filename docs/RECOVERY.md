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

If Redis data is lost, PostgreSQL remains authoritative, but outbox rows already marked
delivered cannot be reconstructed automatically. Do not mark them pending by hand: the
current system has no audited replay operation. Restore the matching Redis volume/AOF or
record the limitation and use synthetic data to rebuild the environment. A production
design needs tested backups, retention, monitoring and an explicit administrative replay
workflow.

## Disposable reset

For synthetic local data only, `docker compose down --volumes` removes both data volumes.
The next `docker compose up --build --detach --wait` creates an empty environment and runs
all migrations. This is a reset, not recovery.
