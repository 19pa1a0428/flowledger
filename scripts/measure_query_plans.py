"""Measure representative PostgreSQL plans against deterministic synthetic data."""
import argparse
import json
import os
from typing import Any

import psycopg


WORKFLOW_QUERY = """
SELECT id, name, owner_id, status, attempts, max_attempts, version, created_at, updated_at
FROM workflows
WHERE owner_id = %s AND status = %s
ORDER BY created_at DESC, id DESC
LIMIT 51
"""

AUDIT_QUERY = """
SELECT e.id, e.workflow_id, e.position, e.status, e.occurred_at, e.attempt,
       e.actor_id, w.owner_id
FROM workflow_events AS e
JOIN workflows AS w ON w.id = e.workflow_id
WHERE e.actor_id = %s AND e.status = %s
ORDER BY e.id DESC
LIMIT 51
"""

CANDIDATE_INDEXES = (
    "CREATE INDEX ix_workflows_owner_status_created_id "
    "ON workflows (owner_id, status, created_at DESC, id DESC)",
    "CREATE INDEX ix_workflow_events_actor_status_id "
    "ON workflow_events (actor_id, status, id DESC)",
)


def summarize_plan(document: dict[str, Any]) -> dict[str, Any]:
    root = document["Plan"]
    nodes: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node["Node Type"])
        for child in node.get("Plans", []):
            visit(child)

    visit(root)
    return {
        "node_types": nodes,
        "total_cost": root["Total Cost"],
        "planning_time_ms": document["Planning Time"],
        "execution_time_ms": document["Execution Time"],
        "shared_hit_blocks": sum_blocks(root, "Shared Hit Blocks"),
        "shared_read_blocks": sum_blocks(root, "Shared Read Blocks"),
    }


def sum_blocks(node: dict[str, Any], key: str) -> int:
    return int(node.get(key, 0)) + sum(sum_blocks(child, key)
                                        for child in node.get("Plans", []))


def explain(cursor, query: str, parameters: tuple[str, str]) -> dict[str, Any]:
    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, parameters)
    return summarize_plan(cursor.fetchone()[0][0])


def seed(cursor, workflow_count: int) -> None:
    cursor.execute("TRUNCATE workflow_events, command_outbox, command_receipts, workflows")
    cursor.execute("""
        INSERT INTO workflows
            (id, name, owner_id, status, attempts, max_attempts, version,
             created_at, updated_at)
        SELECT
            (substr(md5(gs::text), 1, 8) || '-' || substr(md5(gs::text), 9, 4) ||
             '-4' || substr(md5(gs::text), 14, 3) || '-a' ||
             substr(md5(gs::text), 18, 3) || '-' || substr(md5(gs::text), 21, 12))::uuid,
            'synthetic workflow ' || gs,
            'owner-' || (gs %% 100),
            (ARRAY['pending', 'running', 'succeeded', 'failed', 'cancelled'])[
                ((gs / 100) %% 5) + 1
            ],
            CASE WHEN ((gs / 100) %% 5) = 0 THEN 0 ELSE 1 END,
            3,
            ((gs / 100) %% 5),
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + gs * INTERVAL '1 second',
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + gs * INTERVAL '1 second'
        FROM generate_series(1, %s) AS gs
    """, (workflow_count,))
    cursor.execute("""
        INSERT INTO workflow_events
            (workflow_id, position, status, occurred_at, attempt, actor_id)
        SELECT w.id, position,
               (ARRAY['pending', 'running', 'succeeded'])[position + 1],
               w.created_at + position * INTERVAL '100 milliseconds',
               CASE WHEN position = 0 THEN 0 ELSE 1 END,
               CASE WHEN position = 1 THEN 'worker' ELSE w.owner_id END
        FROM workflows AS w
        CROSS JOIN generate_series(0, 2) AS position
    """)
    cursor.execute("ANALYZE workflows")
    cursor.execute("ANALYZE workflow_events")


def measure(database_url: str, workflow_count: int) -> dict[str, Any]:
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for name in ("ix_workflows_owner_status_created_id",
                     "ix_workflow_events_actor_status_id"):
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
        seed(cursor, workflow_count)
        baseline = {
            "workflow_list": explain(cursor, WORKFLOW_QUERY, ("owner-7", "running")),
            "audit_history": explain(cursor, AUDIT_QUERY, ("worker", "running")),
        }
        for statement in CANDIDATE_INDEXES:
            cursor.execute(statement)
        cursor.execute("ANALYZE workflows")
        cursor.execute("ANALYZE workflow_events")
        candidate = {
            "workflow_list": explain(cursor, WORKFLOW_QUERY, ("owner-7", "running")),
            "audit_history": explain(cursor, AUDIT_QUERY, ("worker", "running")),
        }
        for name in baseline:
            if not any("Index" in node for node in candidate[name]["node_types"]):
                raise RuntimeError(f"Candidate {name} plan did not use an index")
            if candidate[name]["total_cost"] >= baseline[name]["total_cost"]:
                raise RuntimeError(f"Candidate {name} plan cost did not improve")
        connection.rollback()  # Leave the disposable migrated schema empty and unchanged.
    return {
        "workflow_count": workflow_count,
        "event_count": workflow_count * 3,
        "baseline": baseline,
        "candidate": candidate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true")
    parser.add_argument("--workflow-count", type=int, default=20_000)
    arguments = parser.parse_args()
    if not arguments.confirm_disposable:
        parser.error("--confirm-disposable is required because the harness truncates tables")
    if not 1_000 <= arguments.workflow_count <= 1_000_000:
        parser.error("--workflow-count must be between 1000 and 1000000")
    database_url = os.getenv("FLOWLEDGER_TEST_POSTGRES_URL")
    if not database_url:
        parser.error("FLOWLEDGER_TEST_POSTGRES_URL is required")
    print(json.dumps(measure(database_url, arguments.workflow_count), indent=2,
                     sort_keys=True))


if __name__ == "__main__":
    main()
