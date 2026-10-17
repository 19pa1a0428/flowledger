"""Retain execution receipts atomically with database effects."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_04"
down_revision = "20261015_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "command_receipts",
        sa.Column("command_id", sa.Uuid(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("command_receipts")
