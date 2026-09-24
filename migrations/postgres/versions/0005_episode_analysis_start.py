"""Preserve the manifest's observable baseline boundary for investigations.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("episodes", sa.Column("analysis_start_ns", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_episode_analysis_start_positive",
        "episodes",
        "analysis_start_ns IS NULL OR analysis_start_ns > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_episode_analysis_start_positive", "episodes", type_="check")
    op.drop_column("episodes", "analysis_start_ns")
