"""PostgreSQL connection and scoped artifact/episode registry helpers."""

import calendar
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.redaction import safe_display_name
from tracecause.storage.artifacts import ArtifactDescriptor
from tracecause.storage.schema import (
    ArtifactReferenceRow,
    EpisodeRow,
    SourceArtifactRow,
    WorkspaceRow,
)


class StorageConflict(ValueError):
    """A scoped row conflicts with an existing authoritative value."""


DEFAULT_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")
ANALYSIS_BUCKET_NS = 10_000_000_000


def closed_analysis_boundary(value: int) -> int:
    """Close the healthy baseline before its end; never include intervention time."""
    if value <= 0:
        raise ValueError("analysis boundary must be positive")
    return value // ANALYSIS_BUCKET_NS * ANALYSIS_BUCKET_NS


def database_url(*, host: str, port: int, database: str, user: str, password_file: Path) -> URL:
    if password_file.is_symlink() or not password_file.is_file():
        raise ValueError("password file is missing or unsafe")
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        raise ValueError("password file is empty")
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def make_engine(url: URL) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=3,
        connect_args={
            "connect_timeout": 3,
            "options": "-c statement_timeout=10000 -c lock_timeout=3000",
        },
    )


def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def health(engine: Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1


def register_episode(session: Session, manifest: EpisodeManifest, manifest_hash: str) -> EpisodeRow:
    if manifest.workspace_id != DEFAULT_WORKSPACE_ID:
        raise StorageConflict("workspace is outside the configured single-workspace scope")
    workspace = session.get(WorkspaceRow, manifest.workspace_id)
    if workspace is None:
        session.add(WorkspaceRow(id=manifest.workspace_id, name="default"))
        session.flush()
    episode = session.get(EpisodeRow, manifest.episode_id)
    baseline_end = manifest.baseline_interval.end if manifest.baseline_interval else None
    raw_analysis_start_ns = (
        calendar.timegm(baseline_end.astimezone(UTC).utctimetuple()) * 1_000_000_000
        + baseline_end.microsecond * 1000
        if baseline_end is not None
        else None
    )
    analysis_start_ns = (
        closed_analysis_boundary(raw_analysis_start_ns)
        if raw_analysis_start_ns is not None
        else None
    )
    if episode is not None:
        if episode.workspace_id != manifest.workspace_id or episode.manifest_hash != manifest_hash:
            raise StorageConflict("episode identity conflicts with existing manifest")
        if episode.analysis_start_ns is None:
            episode.analysis_start_ns = analysis_start_ns
        elif (
            raw_analysis_start_ns is not None
            and episode.analysis_start_ns
            in {
                raw_analysis_start_ns,
                (raw_analysis_start_ns + ANALYSIS_BUCKET_NS - 1)
                // ANALYSIS_BUCKET_NS
                * ANALYSIS_BUCKET_NS,
            }
            and episode.analysis_start_ns != analysis_start_ns
        ):
            # Repair earlier unaligned/rounded-up capture boundaries on idempotent import.
            episode.analysis_start_ns = analysis_start_ns
            episode.active_snapshot_id = None
        elif episode.analysis_start_ns != analysis_start_ns:
            raise StorageConflict("episode analysis boundary conflicts with manifest")
        return episode
    episode = EpisodeRow(
        id=manifest.episode_id,
        workspace_id=manifest.workspace_id,
        display_name=safe_display_name(manifest.display_name),
        origin=manifest.origin.value,
        event_start=manifest.event_interval.start,
        event_end=manifest.event_interval.end,
        analysis_start_ns=analysis_start_ns,
        ingestion_status="QUEUED",
        active_snapshot_id=None,
        data_quality={},
        manifest_hash=manifest_hash,
    )
    session.add(episode)
    session.flush()
    return episode


def register_artifact(
    session: Session,
    workspace_id: UUID,
    episode_id: UUID,
    descriptor: ArtifactDescriptor,
    *,
    kind: str,
    redaction_version: str,
    purpose: str,
) -> SourceArtifactRow:
    row = session.get(SourceArtifactRow, descriptor.content_hash)
    if row is None:
        row = SourceArtifactRow(
            content_hash=descriptor.content_hash,
            workspace_id=workspace_id,
            kind=kind,
            byte_size=descriptor.byte_size,
            relative_storage_key=descriptor.relative_key,
            schema_version="1.0",
            received_at=datetime.now(UTC),
            redaction_version=redaction_version,
        )
        session.add(row)
        session.flush()
    elif (
        row.workspace_id != workspace_id
        or row.byte_size != descriptor.byte_size
        or row.kind != kind
        or row.redaction_version != redaction_version
    ):
        raise StorageConflict("artifact identity conflicts with scope or metadata")
    existing = session.scalar(
        select(ArtifactReferenceRow).where(
            ArtifactReferenceRow.artifact_hash == descriptor.content_hash,
            ArtifactReferenceRow.episode_id == episode_id,
            ArtifactReferenceRow.purpose == purpose,
        )
    )
    if existing is None:
        session.add(
            ArtifactReferenceRow(
                id=uuid4(),
                artifact_hash=descriptor.content_hash,
                episode_id=episode_id,
                purpose=purpose,
            )
        )
    return row
