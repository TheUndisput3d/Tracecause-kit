"""Materialize canonical closed-bucket observations for operational replay."""

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.investigation.validation import as_ns
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import StorageConflict, closed_analysis_boundary
from tracecause.storage.schema import EpisodeRow, SpanRow
from tracecause.telemetry.persistence import materialize_signals
from tracecause.telemetry.signals import BUCKET_NS

REPLAY_VERSION = "closed-bucket-v1"
MAX_REPLAY_CUTOFFS = 120


def materialize_replay(
    factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    workspace_id: UUID,
    episode_id: UUID,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[int, ...]:
    """Keep observation identities stable for every cursor within a closed bucket.

    Source timestamps are unchanged. Only the aggregate query cursor is canonical.
    The version is published after all bounded, idempotent materializations succeed.
    """
    with factory() as session:
        episode = session.get(EpisodeRow, episode_id)
        if (
            episode is None
            or episode.workspace_id != workspace_id
            or episode.ingestion_status != "IMPORTED"
            or episode.analysis_start_ns is None
            or not episode.data_quality.get("operational_application_id")
        ):
            raise StorageConflict("operational replay requires an imported scoped baseline")
        start = closed_analysis_boundary(episode.analysis_start_ns)
        end = as_ns(episode.event_end)
        manifest_hash = episode.manifest_hash
        spans = select(SpanRow.span_id).where(
            SpanRow.workspace_id == workspace_id, SpanRow.episode_id == episode_id
        )
        any_span = bool(session.scalar(select(spans.exists())))
        unknown_availability = bool(
            session.scalar(select(spans.where(SpanRow.available_at.is_(None)).exists()))
        )
        modes: tuple[Literal["event", "available"], ...] = (
            ("event", "available") if any_span and not unknown_availability else ("event",)
        )
    final = end // BUCKET_NS * BUCKET_NS
    cutoffs = range(start + BUCKET_NS, final + BUCKET_NS, BUCKET_NS)
    if not 1 <= len(cutoffs) <= MAX_REPLAY_CUTOFFS:
        raise ValueError("operational replay requires one to 120 closed ten-second cutoffs")
    for cutoff in cutoffs:
        for mode in modes:
            if checkpoint is not None:
                checkpoint()
            materialize_signals(
                factory,
                artifacts,
                workspace_id=workspace_id,
                episode_id=episode_id,
                investigation_start_ns=start,
                as_of_ns=cutoff,
                mode=mode,
                include_history_buckets=cutoff == final,
            )
    with factory.begin() as session:
        episode = session.get(EpisodeRow, episode_id)
        if (
            episode is None
            or episode.workspace_id != workspace_id
            or episode.ingestion_status != "IMPORTED"
            or episode.manifest_hash != manifest_hash
            or episode.analysis_start_ns is None
            or closed_analysis_boundary(episode.analysis_start_ns) != start
            or as_ns(episode.event_end) != end
            or not episode.data_quality.get("operational_application_id")
        ):
            raise StorageConflict("operational replay source changed before version publication")
        episode.data_quality = {
            **episode.data_quality,
            "operational_replay_version": REPLAY_VERSION,
            "operational_replay_modes": list(modes),
        }
    return tuple(cutoffs)
