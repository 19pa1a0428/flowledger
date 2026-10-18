"""Retain terminal domain rejection reasons alongside command receipts."""
from alembic import op
import sqlalchemy as sa

revision = "20261017_05"
down_revision = "20261016_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("command_receipts", sa.Column("rejection", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("command_receipts", "rejection")
