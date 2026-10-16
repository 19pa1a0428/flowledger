"""Add durable command delivery intent.

Revision ID: 20261015_03
Revises: 20261012_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_03"
down_revision = "20261012_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "command_outbox",
        sa.Column("command_id", sa.Uuid(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("command_outbox")
