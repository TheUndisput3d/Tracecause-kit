"""Read-only recovery inventory and reference-checked artifact cleanup."""

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.schema import (
    ArtifactReferenceRow,
    AuditEventRow,
    ChunkRow,
    DocumentRow,
    EpisodeRow,
    EvaluationReportRow,
    InvestigationRow,
    JobRow,
    ModelRegistryRow,
    ObservationRow,
    ObservationSpanRow,
    ReaderLeaseRow,
    SnapshotRow,
    SourceArtifactRow,
    SpanRow,
    WorkspaceRow,
)

TABLE_MODELS = (
    WorkspaceRow,
    EpisodeRow,
    SourceArtifactRow,
    ArtifactReferenceRow,
    SpanRow,
    ObservationRow,
    ObservationSpanRow,
    ReaderLeaseRow,
    DocumentRow,
    ChunkRow,
    SnapshotRow,
    JobRow,
    InvestigationRow,
    ModelRegistryRow,
    EvaluationReportRow,
    AuditEventRow,
)


def backup_metadata(session: Session) -> dict[str, Any]:
    """Return schema revision and counts only; no payloads or secrets."""
    revision = cast(
        str, session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    )
    counts = {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in TABLE_MODELS
    }
    return {
        "schema_revision": revision,
        "captured_at": datetime.now(UTC).isoformat(),
        "row_counts": counts,
    }


def backup_bytes(session: Session) -> bytes:
    return (json.dumps(backup_metadata(session), sort_keys=True, indent=2) + "\n").encode()


def reconciliation_inventory(
    session_factory: sessionmaker[Session], artifacts: ArtifactRepository
) -> dict[str, Any]:
    with session_factory() as session:
        artifact_rows = session.scalars(select(SourceArtifactRow)).all()
        registered_hashes = {row.content_hash for row in artifact_rows}
        missing_artifacts = []
        unreferenced_artifacts = []
        for row in artifact_rows:
            try:
                artifacts.verify(row.content_hash)
            except ValueError:
                missing_artifacts.append(row.content_hash)
            if not _is_referenced(session, row.content_hash):
                unreferenced_artifacts.append(row.content_hash)
        expired = session.scalars(
            select(JobRow.id).where(
                JobRow.status == "RUNNING",
                JobRow.lease_until < datetime.now(UTC),
            )
        ).all()
        building = session.scalars(
            select(SnapshotRow.id).where(SnapshotRow.status == "BUILDING")
        ).all()
        return {
            "missing_artifacts": sorted(missing_artifacts),
            "unreferenced_artifacts": sorted(unreferenced_artifacts),
            "unregistered_artifacts": sorted(artifacts.list_content_hashes() - registered_hashes),
            "expired_job_ids": sorted(str(value) for value in expired),
            "building_snapshot_ids": sorted(str(value) for value in building),
            "temporary_files_eligible": artifacts.cleanup_temporary(dry_run=True),
        }


def _is_referenced(session: Session, content_hash: str) -> bool:
    tables = (
        (ArtifactReferenceRow, ArtifactReferenceRow.artifact_hash),
        (SpanRow, SpanRow.source_artifact_hash),
        (DocumentRow, DocumentRow.source_artifact_hash),
        (ChunkRow, ChunkRow.embedding_artifact_hash),
        (InvestigationRow, InvestigationRow.result_artifact_hash),
        (ModelRegistryRow, ModelRegistryRow.artifact_hash),
        (EvaluationReportRow, EvaluationReportRow.artifact_hash),
    )
    for model, column in tables:
        exists = session.scalar(
            select(func.count()).select_from(model).where(column == content_hash)
        )
        if exists:
            return True
    return False


def delete_unreferenced_artifact(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    content_hash: str,
    *,
    dry_run: bool = True,
) -> bool:
    with session_factory.begin() as session:
        row = session.scalar(
            select(SourceArtifactRow)
            .where(SourceArtifactRow.content_hash == content_hash)
            .with_for_update()
        )
        if row is None:
            raise ValueError("artifact record not found")
        if _is_referenced(session, content_hash):
            raise ValueError("referenced artifact cannot be deleted")
        artifacts.verify(content_hash)
        if dry_run:
            return False
        session.delete(row)
    artifacts.delete(content_hash, referenced=False, dry_run=False)
    return True
