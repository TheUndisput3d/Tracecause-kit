"""Snapshot error codes and reader leases for safe graph retirement.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("error_code", sa.String(length=100), nullable=True))
    op.create_table(
        "reader_leases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.UniqueConstraint("snapshot_id", "owner", name="uq_reader_lease_owner"),
    )
    op.create_index(
        "ix_reader_lease_snapshot_expiry", "reader_leases", ["snapshot_id", "expires_at"]
    )
    op.create_index(
        "uq_snapshot_episode_building",
        "snapshots",
        ["episode_id"],
        unique=True,
        postgresql_where=sa.text("status = 'BUILDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_snapshot_episode_building", table_name="snapshots")
    op.drop_index("ix_reader_lease_snapshot_expiry", table_name="reader_leases")
    op.drop_table("reader_leases")
    op.drop_column("snapshots", "error_code")
