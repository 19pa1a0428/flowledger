"""Persist workflow ownership and audit actors.

Revision ID: 20261024_06
Revises: 20261017_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_06"
down_revision = "20261017_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.add_column(sa.Column(
            "owner_id", sa.String(100), nullable=False, server_default="legacy-system"
        ))
        batch_op.create_check_constraint(
            "ck_workflows_owner_id",
            "length(owner_id) BETWEEN 1 AND 100 AND trim(owner_id) = owner_id",
        )
    with op.batch_alter_table("workflow_events") as batch_op:
        batch_op.add_column(sa.Column(
            "actor_id", sa.String(100), nullable=False, server_default="legacy-system"
        ))
        batch_op.create_check_constraint(
            "ck_workflow_events_actor_id",
            "length(actor_id) BETWEEN 1 AND 100 AND trim(actor_id) = actor_id",
        )
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.alter_column("owner_id", server_default=None)
    with op.batch_alter_table("workflow_events") as batch_op:
        batch_op.alter_column("actor_id", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("workflow_events") as batch_op:
        batch_op.drop_constraint("ck_workflow_events_actor_id", type_="check")
        batch_op.drop_column("actor_id")
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.drop_constraint("ck_workflows_owner_id", type_="check")
        batch_op.drop_column("owner_id")
