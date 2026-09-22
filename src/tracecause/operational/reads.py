"""Scope, validate and join immutable sources before exposing operational reasoning."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from tracecause.api import reads as core_reads
from tracecause.api.errors import ApiError
from tracecause.api.resources import Resources
from tracecause.contracts.models import TemporalMode
from tracecause.graph.maintenance import reader_lease
from tracecause.investigation.validation import as_ns
from tracecause.jobs.api_jobs import scoped_snapshot
from tracecause.operational.api_models import OperationsResponse
from tracecause.operational.handoffs import (
    HandoffEvidence,
    handoff_edges,
    handoff_evidence,
    load_handoffs,
    verify_handoffs,
)
from tracecause.operational.models import OperationalBundle, OperationalEvidence, OperationalFact
from tracecause.operational.projection import assemble, verify
from tracecause.operational.reasoning import (
    EpisodeProfile,
    compare_history,
    degradation_onsets,
    reason_changes,
)
from tracecause.operational.storage import history_membership, load_bundle
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import EpisodeRow, SnapshotRow, SpanRow
from tracecause.telemetry.persistence import compute_persisted_signals
from tracecause.telemetry.signals import BUCKET_NS, SignalFeature


def _profile(
    resources: Resources,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
) -> tuple[EpisodeProfile, OperationalBundle | None, tuple[UUID, ...]]:
    workspace = DEFAULT_WORKSPACE_ID
    with resources.factory() as session:
        episode, snapshot = scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
        hashes = tuple((snapshot.membership or {}).get("operational_hashes", []))
        bundle = load_bundle(session, resources.artifacts, workspace, episode_id, hashes)
        history_ids = history_membership(session, workspace, episode, bundle, snapshot.membership)
        available = session.scalar(
            select(func.max(SpanRow.available_at)).where(
                SpanRow.workspace_id == workspace,
                SpanRow.episode_id == episode_id,
            )
        )
        missing = session.scalar(
            select(SpanRow.span_id)
            .where(
                SpanRow.workspace_id == workspace,
                SpanRow.episode_id == episode_id,
                SpanRow.available_at.is_(None),
            )
            .limit(1)
        )
        observation_ids = set((snapshot.membership or {}).get("observation_ids", []))
        replay_version = (snapshot.membership or {}).get("operational_replay_version")
        replay_modes = (snapshot.membership or {}).get("operational_replay_modes", [])
        if (
            bundle is not None
            and mode is TemporalMode.AVAILABLE
            and "available" not in replay_modes
        ):
            raise ApiError(
                422,
                "availability_projection_unavailable",
                "This snapshot has no availability-time operational measurements. "
                "Use event-time replay or import a new snapshot with recorded availability.",
            )
        handoffs = (
            load_handoffs(
                session,
                resources.artifacts,
                workspace,
                episode_id,
                as_ns(as_of),
                core_reads.event_mode(mode),
            )
            if bundle
            else ()
        )
    verify(resources.driver, workspace, episode_id, snapshot_id, assemble(bundle, workspace))
    graph = core_reads.graph(resources, episode_id, snapshot_id, as_of, mode, None, 3)
    verify_handoffs(
        resources.driver,
        workspace,
        episode_id,
        snapshot_id,
        handoffs,
        as_ns(as_of),
        core_reads.event_mode(mode),
    )
    visible = {node.service_id for node in graph.nodes}
    extra_edges = [
        edge
        for edge in handoff_edges(handoffs)
        if edge.source_service_id in visible and edge.target_service_id in visible
    ]
    cap = resources.settings.graph_max_edges
    combined = [*graph.edges, *extra_edges]
    graph = graph.model_copy(
        update={"edges": combined[:cap], "truncated": graph.truncated or len(combined) > cap}
    )
    facts = tuple(
        OperationalFact(
            event=event,
            service_key=event.service.key(workspace),
            evidence_id=f"operation:{event.id}",
            artifact_hash=hashes[0],
            application_id=bundle.application_id if bundle else "unrecorded",
            episode_id=episode_id,
            snapshot_id=snapshot_id,
        )
        for event in (bundle.events if bundle else [])
        if event.occurred_at <= as_of
        and (mode is TemporalMode.EVENT or event.available_at <= as_of)
        and event.service.key(workspace) in visible
    )
    signals: tuple[SignalFeature, ...] = ()
    onsets: dict[str, int | None] = {}
    if (
        bundle is not None
        and episode.analysis_start_ns is not None
        and as_ns(as_of) >= episode.analysis_start_ns
    ):
        report = compute_persisted_signals(
            resources.factory,
            workspace_id=workspace,
            episode_id=episode_id,
            as_of_ns=(as_ns(as_of) // BUCKET_NS * BUCKET_NS)
            if replay_version == "closed-bucket-v1"
            else as_ns(as_of),
            mode=core_reads.event_mode(mode),
        )
        populated = {item.id for item in report.observations if item.sample_count > 0}
        source_signals = tuple(
            replace(
                signal,
                baseline_observation_ids=tuple(
                    value for value in signal.baseline_observation_ids if value in populated
                ),
            )
            for signal in report.signals
        )
        onsets = degradation_onsets(report.signals, report.observations)
        # Every exposed statistic has a resolvable, snapshot-pinned aggregate and baseline.
        signals = tuple(
            signal
            for signal in source_signals
            if str(signal.analysis_observation_id) in observation_ids
            and all(str(value) in observation_ids for value in signal.baseline_observation_ids)
        )
        visible_observations = resources.reader.eligible_observation_summaries(
            workspace_id=workspace,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_ns(as_of),
            mode=core_reads.event_mode(mode),
        )
        verified = {row["observation_id"] for row in visible_observations}
        signals = tuple(
            signal
            for signal in signals
            if str(signal.analysis_observation_id) in verified
            and all(str(value) in verified for value in signal.baseline_observation_ids)
        )
    return (
        EpisodeProfile(
            workspace_id=workspace,
            application_id=bundle.application_id if bundle else "unrecorded",
            environment=bundle.events[0].service.environment if bundle else "unrecorded",
            display_name=episode.display_name,
            started_at=episode.event_start,
            ended_at=episode.event_end,
            available_at=max([available, *(event.available_at for event in bundle.events)])
            if available is not None and missing is None and bundle
            else None,
            graph=graph,
            facts=facts,
            signals=signals,
            degradation_onset_ns=onsets,
        ),
        bundle,
        history_ids,
    )


def operations(
    resources: Resources,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
    symptom_service_id: str | None,
) -> OperationsResponse:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    with reader_lease(resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id, renew=True):
        profile, bundle, history_ids = _profile(resources, episode_id, snapshot_id, as_of, mode)
        previous = []
        for identifier in history_ids:
            with resources.factory() as session:
                snapshot = session.get(SnapshotRow, identifier)
                episode = session.get(EpisodeRow, snapshot.episode_id) if snapshot else None
                if episode is None:
                    raise ApiError(
                        503, "history_unavailable", "Pinned historical evidence is unavailable."
                    )
            with reader_lease(
                resources.factory, DEFAULT_WORKSPACE_ID, episode.id, identifier, renew=True
            ):
                historical, _, _ = _profile(
                    resources, episode.id, identifier, episode.event_end, TemporalMode.EVENT
                )
                previous.append(historical)
        current = None
        if bundle is not None and profile.facts:
            current = bundle.model_copy(update={"events": [fact.event for fact in profile.facts]})
        material = assemble(current, DEFAULT_WORKSPACE_ID)
        selected = symptom_service_id or next(
            (node.service_id for node in profile.graph.nodes), None
        )
        if selected and selected not in {node.service_id for node in profile.graph.nodes}:
            raise ApiError(404, "not_found", "The context service is not visible at this cutoff.")
        return OperationsResponse(
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of=as_of,
            temporal_mode=mode,
            application_id=bundle.application_id if bundle else None,
            application_name=bundle.application_name if bundle else None,
            topology=bundle.topology if bundle else None,
            graph=profile.graph,
            nodes=list(material.nodes),
            edges=list(material.edges),
            facts=list(profile.facts),
            change_reasoning=reason_changes(
                profile.graph,
                profile.facts,
                profile.signals,
                selected,
                degradation_onset_ns=profile.degradation_onset_ns,
            )
            if selected
            else None,
            history=compare_history(profile, previous, as_of=as_of, temporal_mode=mode),
            limitations=[
                "Operational records describe reported state; "
                "associations are not proof of causality.",
                "Historical snapshots are pinned at publication; "
                "later imports do not alter this result.",
                "Measurements require a complete, pinned ten-second aggregate "
                "and baseline at the cutoff.",
            ],
        )


def evidence(
    resources: Resources,
    identifier: str,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
) -> OperationalEvidence:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    with reader_lease(resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id, renew=True):
        with resources.factory() as session:
            _, snapshot = scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
            hashes = tuple((snapshot.membership or {}).get("operational_hashes", []))
            bundle = load_bundle(
                session, resources.artifacts, DEFAULT_WORKSPACE_ID, episode_id, hashes
            )
            event = (
                next(
                    (event for event in bundle.events if f"operation:{event.id}" == identifier),
                    None,
                )
                if bundle
                else None
            )
            if (
                event is None
                or event.occurred_at > as_of
                or (mode is TemporalMode.AVAILABLE and event.available_at > as_of)
            ):
                raise ApiError(
                    404, "not_found", "The operational record is not visible in this scope."
                )
            visible = select(SpanRow.span_id).where(
                SpanRow.workspace_id == DEFAULT_WORKSPACE_ID,
                SpanRow.episode_id == episode_id,
                SpanRow.service_key == event.service.key(DEFAULT_WORKSPACE_ID),
                SpanRow.end_ns <= as_ns(as_of),
            )
            if mode is TemporalMode.AVAILABLE:
                visible = visible.where(SpanRow.available_at <= as_of)
            if session.scalar(visible.limit(1)) is None:
                raise ApiError(404, "not_found", "The service is not visible at this cutoff.")
        assert bundle is not None
        verify(
            resources.driver,
            DEFAULT_WORKSPACE_ID,
            episode_id,
            snapshot_id,
            assemble(bundle, DEFAULT_WORKSPACE_ID),
        )
        return OperationalEvidence(
            evidence_id=identifier,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of=as_of,
            artifact_hash=hashes[0],
            application_id=bundle.application_id,
            event=event,
            source_json=event.model_dump_json(indent=2),
        )


def handoff_source(
    resources: Resources,
    identifier: str,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
) -> HandoffEvidence:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    with reader_lease(resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id, renew=True):
        with resources.factory() as session:
            _, snapshot = scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
            if not (snapshot.membership or {}).get("operational_hashes"):
                raise ApiError(404, "not_found", "Message evidence is not in this snapshot.")
            records = load_handoffs(
                session,
                resources.artifacts,
                DEFAULT_WORKSPACE_ID,
                episode_id,
                as_ns(as_of),
                core_reads.event_mode(mode),
            )
        verify_handoffs(
            resources.driver,
            DEFAULT_WORKSPACE_ID,
            episode_id,
            snapshot_id,
            records,
            as_ns(as_of),
            core_reads.event_mode(mode),
        )
        record = next((record for record in records if f"handoff:{record.id}" == identifier), None)
        if record is None:
            raise ApiError(404, "not_found", "Message evidence is not visible at this cutoff.")
        return handoff_evidence(record, snapshot_id, as_of)
