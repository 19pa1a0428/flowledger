"""Create workflow snapshot and event history tables.

Revision ID: 20261009_01
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_01"
down_revision = None
branch_labels = None
depends_on = None

STATUSES = "'pending', 'running', 'succeeded', 'failed', 'cancelled'"

def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_workflows_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_workflows_max_attempts_range"),
        sa.CheckConstraint("attempts <= max_attempts", name="ck_workflows_attempts_within_limit"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_workflows_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_workflow_events_position_nonnegative"),
        sa.CheckConstraint("attempt >= 0", name="ck_workflow_events_attempt_nonnegative"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_workflow_events_status"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "position", name="uq_workflow_events_position"),
    )
    op.create_index("ix_workflow_events_workflow_id", "workflow_events", ["workflow_id"])

def downgrade() -> None:
    op.drop_index("ix_workflow_events_workflow_id", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_table("workflows")
