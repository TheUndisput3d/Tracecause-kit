"""Persist immutable signal observations and their exact span support in PostgreSQL."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import (
    StorageConflict,
    closed_analysis_boundary,
    register_artifact,
)
from tracecause.storage.schema import (
    ArtifactReferenceRow,
    EpisodeRow,
    ObservationRow,
    ObservationSpanRow,
    SourceArtifactRow,
    SpanRow,
)
from tracecause.telemetry.signals import (
    BASELINE_NS,
    BUCKET_NS,
    ComputedObservation,
    SignalReport,
    SpanFact,
    compute_signals,
)


@dataclass(frozen=True)
class MaterializedSignals:
    report_hash: str
    input_hash: str
    episode_id: UUID
    observation_count: int
    signal_count: int
    closed_boundary_ns: int


def _fact(row: SpanRow) -> SpanFact:
    return SpanFact(
        workspace_id=row.workspace_id,
        episode_id=row.episode_id,
        trace_id=row.trace_id,
        span_id=row.span_id,
        parent_span_id=row.parent_span_id,
        service_key=row.service_key,
        kind=row.kind,
        status=row.status,
        start_ns=row.start_ns,
        end_ns=row.end_ns,
        available_at=row.available_at,
        safe_attributes=row.safe_attributes,
        quality_flags=tuple(row.quality_flags),
        normalized_hash=row.normalized_hash,
        artifact_hash=row.source_artifact_hash,
        source_record_offset=row.source_record_offset,
        source_line_number=row.source_line_number,
        source_resource_index=row.source_resource_index,
        source_scope_index=row.source_scope_index,
        source_span_index=row.source_span_index,
    )


def _encode_report(report: SignalReport) -> bytes:
    return (
        json.dumps(
            report.to_jsonable(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def _require_availability(session: Session, workspace_id: UUID, episode_id: UUID) -> None:
    """Check episode capability even when the requested event window is empty."""
    scoped = select(SpanRow.span_id).where(
        SpanRow.workspace_id == workspace_id, SpanRow.episode_id == episode_id
    )
    if not session.scalar(select(scoped.exists())) or session.scalar(
        select(scoped.where(SpanRow.available_at.is_(None)).exists())
    ):
        raise ValueError("reliable per-record availability is absent")


def _observation_row(
    item: ComputedObservation, signals: dict[UUID, dict[str, Any]]
) -> dict[str, Any]:
    attributes = dict(item.attributes)
    if item.id in signals:
        attributes["signal"] = signals[item.id]
    return {
        "id": item.id,
        "episode_id": item.episode_id,
        "rule_version": item.rule_version,
        "service_key": item.service_key,
        "caller_service_key": item.caller_service_key,
        "callee_service_key": item.callee_service_key,
        "observation_type": item.observation_type,
        "start_ns": item.start_ns,
        "end_ns": item.end_ns,
        "sample_count": item.sample_count,
        "error_count": item.error_count,
        "p50_ms": item.p50_ms,
        "p95_ms": item.p95_ms,
        "attributes": attributes,
        "provenance_hash": item.provenance_hash,
        "available_at": item.available_at,
    }


def materialize_signals(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    *,
    workspace_id: UUID,
    episode_id: UUID,
    investigation_start_ns: int,
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
    include_history_buckets: bool = False,
) -> MaterializedSignals:
    boundary = as_of_ns // BUCKET_NS * BUCKET_NS
    if (
        as_of_ns < investigation_start_ns
        or as_of_ns > 2**63 - 1
        or investigation_start_ns <= BASELINE_NS
        or investigation_start_ns % BUCKET_NS
    ):
        raise ValueError("invalid investigation window")
    with session_factory() as session:
        episode = session.get(EpisodeRow, episode_id)
        if (
            episode is None
            or episode.workspace_id != workspace_id
            or episode.ingestion_status != "IMPORTED"
        ):
            raise StorageConflict("episode is missing, out of scope or not imported")
        if mode == "available":
            _require_availability(session, workspace_id, episode_id)
        rows = session.scalars(
            select(SpanRow).where(
                SpanRow.episode_id == episode_id,
                SpanRow.workspace_id == workspace_id,
                SpanRow.end_ns >= investigation_start_ns - BASELINE_NS,
                SpanRow.end_ns < boundary,
            )
        ).all()
        facts = tuple(_fact(row) for row in rows)
    report = compute_signals(
        facts,
        workspace_id=workspace_id,
        episode_id=episode_id,
        investigation_start_ns=investigation_start_ns,
        as_of_ns=as_of_ns,
        mode=mode,
        include_history_buckets=include_history_buckets,
    )
    descriptor = artifacts.put_bytes(_encode_report(report))
    signals = {
        item.analysis_observation_id: {
            **asdict(item),
            "analysis_observation_id": str(item.analysis_observation_id),
            "baseline_observation_ids": [str(value) for value in item.baseline_observation_ids],
            "analysis_bucket_ids": [str(value) for value in item.analysis_bucket_ids],
        }
        for item in report.signals
    }
    with session_factory.begin() as session:
        episode = session.get(EpisodeRow, episode_id)
        if (
            episode is None
            or episode.workspace_id != workspace_id
            or episode.ingestion_status != "IMPORTED"
        ):
            raise StorageConflict("episode changed before signal publication")
        register_artifact(
            session,
            workspace_id,
            episode_id,
            descriptor,
            kind="trace_signal_report",
            redaction_version="derived-from-redacted-v1",
            purpose="signal_report",
        )
        for start in range(0, len(report.observations), 200):
            chunk = report.observations[start : start + 200]
            rows_to_write = [_observation_row(item, signals) for item in chunk]
            session.execute(
                pg_insert(ObservationRow).values(rows_to_write).on_conflict_do_nothing()
            )
            stored = session.scalars(
                select(ObservationRow).where(ObservationRow.id.in_([item.id for item in chunk]))
            ).all()
            existing = {item.id: item for item in stored}
            for item in chunk:
                row = existing[item.id]
                if row.provenance_hash != item.provenance_hash or row.episode_id != episode_id:
                    raise StorageConflict("observation identity conflicts with stored provenance")
        support_rows = [
            {
                "observation_id": item.id,
                "episode_id": episode_id,
                "trace_id": trace_id,
                "span_id": span_id,
                "role": role,
            }
            for item in report.observations
            for trace_id, span_id, role in item.supports
        ]
        for start in range(0, len(support_rows), 500):
            session.execute(
                pg_insert(ObservationSpanRow)
                .values(support_rows[start : start + 500])
                .on_conflict_do_nothing()
            )
    return MaterializedSignals(
        descriptor.content_hash,
        report.input_hash,
        episode_id,
        len(report.observations),
        len(report.signals),
        report.closed_boundary_ns,
    )


def compute_persisted_signals(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: UUID,
    episode_id: UUID,
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
) -> SignalReport:
    """Recompute one cutoff from authoritative spans and the manifest baseline boundary."""
    with session_factory() as session:
        episode = session.get(EpisodeRow, episode_id)
        if (
            episode is None
            or episode.workspace_id != workspace_id
            or episode.ingestion_status != "IMPORTED"
            or episode.analysis_start_ns is None
        ):
            raise StorageConflict("episode lacks an imported baseline boundary")
        start = closed_analysis_boundary(episode.analysis_start_ns)
        boundary = as_of_ns // BUCKET_NS * BUCKET_NS
        if mode == "available":
            _require_availability(session, workspace_id, episode_id)
        rows = session.scalars(
            select(SpanRow).where(
                SpanRow.episode_id == episode_id,
                SpanRow.workspace_id == workspace_id,
                SpanRow.end_ns >= start - BASELINE_NS,
                SpanRow.end_ns < boundary,
            )
        ).all()
    return compute_signals(
        tuple(_fact(row) for row in rows),
        workspace_id=workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=as_of_ns,
        mode=mode,
    )


def inspect_signals(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    *,
    workspace_id: UUID,
    episode_id: UUID,
    report_hash: str,
    observation_id: UUID | None = None,
) -> dict[str, Any]:
    with session_factory() as session:
        artifact = session.get(SourceArtifactRow, report_hash)
        reference = session.scalar(
            select(ArtifactReferenceRow).where(
                ArtifactReferenceRow.artifact_hash == report_hash,
                ArtifactReferenceRow.episode_id == episode_id,
                ArtifactReferenceRow.purpose == "signal_report",
            )
        )
        episode = session.get(EpisodeRow, episode_id)
        if (
            artifact is None
            or reference is None
            or episode is None
            or artifact.workspace_id != workspace_id
            or episode.workspace_id != workspace_id
            or artifact.kind != "trace_signal_report"
        ):
            raise StorageConflict("signal report is missing or outside scope")
    document = cast(dict[str, Any], json.loads(artifacts.get_bytes(report_hash)))
    if document.get("input_hash") is None or document.get("episode_id") != str(episode_id):
        raise StorageConflict("signal report identity mismatch")
    if observation_id is None:
        return document
    for item in document["observations"]:
        if item["id"] == str(observation_id):
            return cast(dict[str, Any], item)
    raise ValueError("observation is absent from report")
