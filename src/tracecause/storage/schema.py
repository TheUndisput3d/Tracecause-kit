"""Authoritative relational schema; graph projections are rebuildable."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EpisodeRow(Base):
    __tablename__ = "episodes"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120))
    origin: Mapped[str] = mapped_column(String(30))
    event_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    analysis_start_ns: Mapped[int | None] = mapped_column(BigInteger)
    ingestion_status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    active_snapshot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("snapshots.id", use_alter=True, name="fk_episode_active_snapshot"),
    )
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("event_end > event_start", name="ck_episode_event_range"),
        CheckConstraint(
            "analysis_start_ns IS NULL OR analysis_start_ns > 0",
            name="ck_episode_analysis_start_positive",
        ),
        Index("ix_episode_workspace_created", "workspace_id", "created_at", "id"),
    )


class SourceArtifactRow(Base):
    __tablename__ = "source_artifacts"
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    relative_storage_key: Mapped[str] = mapped_column(String(130))
    schema_version: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    redaction_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_artifact_nonnegative_size"),
        Index("ix_artifact_workspace_created", "workspace_id", "created_at"),
    )


class ArtifactReferenceRow(Base):
    __tablename__ = "artifact_references"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    artifact_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash"), nullable=False
    )
    episode_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("episodes.id"))
    purpose: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("artifact_hash", "episode_id", "purpose", name="uq_artifact_reference"),
        Index("ix_artifact_reference_episode", "episode_id"),
    )


class SpanRow(Base):
    __tablename__ = "spans"
    episode_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("episodes.id"), primary_key=True
    )
    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    namespace: Mapped[str | None] = mapped_column(String(100))
    environment: Mapped[str | None] = mapped_column(String(100))
    service_name: Mapped[str | None] = mapped_column(String(100))
    service_key: Mapped[str | None] = mapped_column(String(64))
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    operation: Mapped[str] = mapped_column(String(500))
    start_ns: Mapped[int] = mapped_column(BigInteger)
    end_ns: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    safe_attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_artifact_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash"), nullable=False
    )
    source_record_offset: Mapped[int] = mapped_column(BigInteger)
    source_line_number: Mapped[int] = mapped_column(Integer)
    source_resource_index: Mapped[int] = mapped_column(Integer)
    source_scope_index: Mapped[int] = mapped_column(Integer)
    source_span_index: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("end_ns >= start_ns", name="ck_span_time_order"),
        Index("ix_span_episode_service_end", "episode_id", "service_key", "end_ns"),
        Index("ix_span_episode_trace_parent", "episode_id", "trace_id", "parent_span_id"),
    )


class ObservationRow(Base):
    __tablename__ = "observations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    episode_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("episodes.id"), nullable=False
    )
    rule_version: Mapped[str] = mapped_column(String(40))
    service_key: Mapped[str | None] = mapped_column(String(64))
    caller_service_key: Mapped[str | None] = mapped_column(String(64))
    callee_service_key: Mapped[str | None] = mapped_column(String(64))
    observation_type: Mapped[str] = mapped_column(String(30))
    start_ns: Mapped[int] = mapped_column(BigInteger)
    end_ns: Mapped[int] = mapped_column(BigInteger)
    sample_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    p50_ms: Mapped[float | None] = mapped_column(Float)
    p95_ms: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provenance_hash: Mapped[str] = mapped_column(String(64))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("end_ns > start_ns", name="ck_observation_time_order"),
        CheckConstraint(
            "sample_count >= error_count AND error_count >= 0", name="ck_observation_counts"
        ),
        Index("ix_observation_episode_end", "episode_id", "end_ns"),
    )


class ObservationSpanRow(Base):
    __tablename__ = "observation_spans"
    observation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("observations.id"), primary_key=True
    )
    episode_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["episode_id", "trace_id", "span_id"],
            ["spans.episode_id", "spans.trace_id", "spans.span_id"],
            name="fk_observation_span_source",
        ),
    )


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    source_artifact_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_type: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    __table_args__ = (
        Index("ix_document_workspace_published", "workspace_id", "published_at"),
        UniqueConstraint("workspace_id", "source_id", "version", name="uq_document_source_version"),
    )


class ChunkRow(Base):
    __tablename__ = "chunks"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(40))
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    normalized_text_hash: Mapped[str] = mapped_column(String(64))
    embedding_artifact_hash: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash")
    )
    chunking_version: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        CheckConstraint("end_char > start_char AND start_char >= 0", name="ck_chunk_offsets"),
        UniqueConstraint("document_id", "version", "start_char", "end_char", name="uq_chunk_range"),
        Index("ix_chunk_document_start", "document_id", "start_char"),
    )


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    episode_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("episodes.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    membership: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        Index("ix_snapshot_episode_status", "episode_id", "status"),
        Index(
            "uq_snapshot_episode_building",
            "episode_id",
            unique=True,
            postgresql_where=text("status = 'BUILDING'"),
        ),
    )


class ReaderLeaseRow(Base):
    __tablename__ = "reader_leases"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    episode_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("episodes.id"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("snapshots.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("ix_reader_lease_snapshot_expiry", "snapshot_id", "expires_at"),
        UniqueConstraint("snapshot_id", "owner", name="uq_reader_lease_owner"),
    )


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    episode_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("episodes.id"))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    import_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[str] = mapped_column(String(40), default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "episode_id", "idempotency_key", name="uq_job_idempotency"
        ),
        Index("ix_job_claim", "status", "next_retry_at", "lease_until", "created_at"),
        Index(
            "uq_api_job_key",
            "workspace_id",
            "kind",
            "idempotency_key",
            unique=True,
            postgresql_where=text("kind IN ('API_IMPORT', 'INVESTIGATE')"),
        ),
        CheckConstraint("attempts >= 0", name="ck_job_attempts"),
    )


class InvestigationRow(Base):
    __tablename__ = "investigations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    episode_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("episodes.id"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("snapshots.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_artifact_hash: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_investigation_episode_created", "episode_id", "created_at"),)


class ModelRegistryRow(Base):
    __tablename__ = "model_registry"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    feature_schema_version: Mapped[str] = mapped_column(String(40))
    artifact_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash"), nullable=False
    )
    config_hash: Mapped[str] = mapped_column(String(64))
    metric_report_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationReportRow(Base):
    __tablename__ = "evaluation_reports"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    artifact_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_artifacts.content_hash"), nullable=False
    )
    private_run_reference: Mapped[str | None] = mapped_column(String(100))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    split_hash: Mapped[str] = mapped_column(String(64))
    model_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100))
    actor_type: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(40))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_audit_timestamp", "timestamp"),)
