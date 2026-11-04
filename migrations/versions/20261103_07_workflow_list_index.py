"""Index the measured owner/status workflow-list access path.

Revision ID: 20261103_07
Revises: 20261024_06
"""
from alembic import op
import sqlalchemy as sa


revision = "20261103_07"
down_revision = "20261024_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_workflows_owner_status_created_id",
        "workflows",
        ["owner_id", "status", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_workflows_owner_status_created_id", table_name="workflows")
