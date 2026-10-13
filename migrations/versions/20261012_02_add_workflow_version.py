"""Add an optimistic concurrency version to workflow snapshots.

Revision ID: 20261012_02
Revises: 20261009_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_02"
down_revision = "20261009_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "ck_workflows_version_nonnegative", "version >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.drop_constraint("ck_workflows_version_nonnegative", type_="check")
        batch_op.drop_column("version")
