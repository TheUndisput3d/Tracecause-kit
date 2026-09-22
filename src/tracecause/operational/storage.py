"""Immutable operational receipts bound to an episode's declared source manifest."""

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.contracts.models import EpisodeManifest
from tracecause.operational.models import OperationalBundle
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import StorageConflict, register_artifact
from tracecause.storage.schema import (
    ArtifactReferenceRow,
    EpisodeRow,
    SnapshotRow,
    SourceArtifactRow,
    SpanRow,
)

PURPOSE = "operational_bundle"


def ingest_operations(
    factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    manifest: EpisodeManifest,
    sources: tuple[Path, ...],
) -> None:
    inputs = [
        (metadata, path)
        for metadata, path in zip(manifest.sources, sources, strict=True)
        if metadata.kind == "operational_json"
    ]
    if len(inputs) > 1:
        raise ValueError("one operational bundle per episode is supported")
    for metadata, path in inputs:
        raw = path.read_bytes()
        if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != metadata.sha256:
            raise ValueError("operational source differs from manifest or exceeds limit")
        bundle = OperationalBundle.model_validate_json(raw)
        with factory.begin() as session:
            episode = session.get(EpisodeRow, manifest.episode_id)
            if episode is None or episode.workspace_id != manifest.workspace_id:
                raise StorageConflict("operational episode is unavailable")
            keys = set(
                session.scalars(
                    select(SpanRow.service_key).where(
                        SpanRow.workspace_id == manifest.workspace_id,
                        SpanRow.episode_id == manifest.episode_id,
                    )
                )
            )
            if any(event.service.key(manifest.workspace_id) not in keys for event in bundle.events):
                raise ValueError("operational service lacks matching telemetry identity")
            if any(event.occurred_at > manifest.event_interval.end for event in bundle.events):
                raise ValueError("operational event exceeds the declared episode interval")
            previous = session.scalars(
                select(ArtifactReferenceRow.artifact_hash).where(
                    ArtifactReferenceRow.episode_id == manifest.episode_id,
                    ArtifactReferenceRow.purpose == PURPOSE,
                )
            ).all()
            if previous and previous != [metadata.sha256]:
                raise StorageConflict("operational source membership is immutable")
            descriptor = artifacts.put_bytes(raw)
            register_artifact(
                session,
                manifest.workspace_id,
                manifest.episode_id,
                descriptor,
                kind=PURPOSE,
                redaction_version="operational-allowlist-v1",
                purpose=PURPOSE,
            )
            episode.data_quality = {
                **episode.data_quality,
                "operational_application_id": bundle.application_id,
                "operational_environment": bundle.events[0].service.environment,
            }


def source_hashes(session: Session, workspace_id: UUID, episode_id: UUID) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(ArtifactReferenceRow.artifact_hash)
            .join(
                SourceArtifactRow,
                SourceArtifactRow.content_hash == ArtifactReferenceRow.artifact_hash,
            )
            .where(
                ArtifactReferenceRow.episode_id == episode_id,
                ArtifactReferenceRow.purpose == PURPOSE,
                SourceArtifactRow.workspace_id == workspace_id,
                SourceArtifactRow.kind == PURPOSE,
            )
            .order_by(ArtifactReferenceRow.artifact_hash)
        ).all()
    )


def load_bundle(
    session: Session,
    artifacts: ArtifactRepository | None,
    workspace_id: UUID,
    episode_id: UUID,
    hashes: tuple[str, ...],
) -> OperationalBundle | None:
    if not hashes:
        return None
    if len(hashes) != 1 or artifacts is None:
        raise StorageConflict("operational source membership is invalid")
    registered = source_hashes(session, workspace_id, episode_id)
    if not set(hashes) <= set(registered):
        raise StorageConflict("operational source is outside snapshot episode")
    raw = artifacts.get_bytes(hashes[0])
    if hashlib.sha256(raw).hexdigest() != hashes[0]:
        raise StorageConflict("operational source hash differs")
    return OperationalBundle.model_validate_json(raw)


def history_membership(
    session: Session,
    workspace: UUID,
    episode: EpisodeRow,
    bundle: OperationalBundle | None,
    membership: dict[str, Any] | None,
) -> tuple[UUID, ...]:
    """Pin earlier ready snapshots once; replay never consults newer active pointers."""
    if bundle is None:
        return ()
    if membership is not None:
        identifiers = tuple(UUID(value) for value in membership.get("history_snapshot_ids", []))
        if len(identifiers) > 32 or len(set(identifiers)) != len(identifiers):
            raise StorageConflict("historical snapshot membership is invalid")
        for identifier in identifiers:
            snapshot = session.get(SnapshotRow, identifier)
            previous = session.get(EpisodeRow, snapshot.episode_id) if snapshot else None
            if (
                snapshot is None
                or previous is None
                or snapshot.workspace_id != workspace
                or previous.workspace_id != workspace
                or snapshot.status != "READY"
                or previous.event_end > episode.event_start
                or previous.id == episode.id
                or previous.data_quality.get("operational_application_id") != bundle.application_id
                or previous.data_quality.get("operational_environment")
                != bundle.events[0].service.environment
            ):
                raise StorageConflict(
                    "historical snapshot membership is unavailable or outside scope"
                )
        return identifiers
    return tuple(
        session.scalars(
            select(SnapshotRow.id)
            .join(EpisodeRow, EpisodeRow.active_snapshot_id == SnapshotRow.id)
            .where(
                EpisodeRow.workspace_id == workspace,
                SnapshotRow.workspace_id == workspace,
                EpisodeRow.id != episode.id,
                EpisodeRow.event_end <= episode.event_start,
                EpisodeRow.data_quality["operational_application_id"].as_string()
                == bundle.application_id,
                EpisodeRow.data_quality["operational_environment"].as_string()
                == bundle.events[0].service.environment,
                SnapshotRow.status == "READY",
            )
            .order_by(EpisodeRow.event_end.desc(), SnapshotRow.id)
            .limit(32)
            .with_for_update(of=SnapshotRow)
        ).all()
    )
