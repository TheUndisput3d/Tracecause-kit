"""Add scoped immutable document source identity.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_id", sa.String(length=80), nullable=False))
    op.create_unique_constraint(
        "uq_document_source_version", "documents", ["workspace_id", "source_id", "version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_source_version", "documents", type_="unique")
    op.drop_column("documents", "source_id")
