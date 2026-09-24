"""Bind API idempotency across episodes within workspace and operation kind."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_api_job_key",
        "jobs",
        ["workspace_id", "kind", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("kind IN ('API_IMPORT', 'INVESTIGATE')"),
    )


def downgrade() -> None:
    op.drop_index("uq_api_job_key", table_name="jobs")
    op.drop_column("jobs", "finished_at")
    op.drop_column("jobs", "started_at")
