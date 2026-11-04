# PostgreSQL query-plan measurement

The performance harness uses the real Alembic-migrated schema and deterministic synthetic data.
It inserts 20,000 workflows across 100 owners and five statuses plus 60,000 history events. It
then runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for the owner/status workflow list and the
actor/status audit query before and after two candidate indexes.

The harness requires an explicitly disposable database because it truncates FlowLedger tables:

```bash
export FLOWLEDGER_DATABASE_URL=postgresql+psycopg://flowledger:flowledger@localhost/flowledger_test
export FLOWLEDGER_TEST_POSTGRES_URL="$FLOWLEDGER_DATABASE_URL"
python -m alembic upgrade head
python scripts/measure_query_plans.py --confirm-disposable
```

It rolls back the synthetic rows and candidate indexes after measurement. CI runs the same command
against PostgreSQL 17 and fails if a candidate plan does not use an index or does not reduce the
planner's total cost. Execution time and buffer counts are printed as observations but are not
gates because shared CI hosts and cache warming introduce noise. No performance result should be
claimed until a completed run supplies its output.
