"""Retain exact source membership for immutable snapshot reconstruction."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("membership", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("snapshots", "membership")
