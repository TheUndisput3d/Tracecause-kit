"""Scoped, bounded read facade over authoritative storage and graph services."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from tracecause.api.errors import ApiError
from tracecause.api.pagination import decode_cursor, encode_cursor
from tracecause.api.resources import Resources
from tracecause.contracts.models import (
    DataOrigin,
    EpisodePage,
    EpisodeView,
    EvaluationPage,
    EvaluationReport,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphResponse,
    InvestigationPage,
    InvestigationView,
    JobStatus,
    JobView,
    KnowledgeChecksResponse,
    KnowledgeResponse,
    PublicError,
    SeriesPoint,
    SeriesResponse,
    TemporalMode,
    TimeInterval,
)
from tracecause.graph.knowledge import assemble_knowledge, reachable_checks
from tracecause.graph.maintenance import reader_lease
from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.investigation.templates import evidence_from_candidate, ns_datetime
from tracecause.investigation.validation import as_ns
from tracecause.jobs.api_jobs import scoped_episode, scoped_snapshot
from tracecause.retrieval.engine import RetrievalRequest, corpus_from_reader
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import (
    EpisodeRow,
    EvaluationReportRow,
    InvestigationRow,
    JobRow,
    ObservationRow,
    SnapshotRow,
    SourceArtifactRow,
    SpanRow,
)
from tracecause.telemetry.signals import BASELINE_NS


def event_mode(mode: TemporalMode) -> Literal["event", "available"]:
    return "available" if mode is TemporalMode.AVAILABLE else "event"


def episode_view(session: Session, row: EpisodeRow) -> EpisodeView:
    any_span = select(SpanRow.span_id).where(SpanRow.episode_id == row.id).exists()
    missing = (
        select(SpanRow.span_id)
        .where(SpanRow.episode_id == row.id, SpanRow.available_at.is_(None))
        .exists()
    )
    spans, services = session.execute(
        select(func.count(), func.count(SpanRow.service_key.distinct()))
        .select_from(SpanRow)
        .where(SpanRow.episode_id == row.id)
    ).one()
    latest = session.scalar(
        select(InvestigationRow.status)
        .where(InvestigationRow.episode_id == row.id)
        .order_by(InvestigationRow.created_at.desc())
        .limit(1)
    )
    return EpisodeView(
        default_as_of=row.data_quality.get("default_as_of"),
        default_symptom_service_id=row.data_quality.get("default_symptom_service_id"),
        episode_id=row.id,
        display_name=row.display_name,
        origin=DataOrigin(row.origin),
        event_interval=TimeInterval(start=row.event_start, end=row.event_end),
        ingestion_status=row.ingestion_status,
        active_snapshot_id=row.active_snapshot_id,
        created_at=row.created_at,
        service_count=services,
        valid_span_count=spans,
        baseline_interval=TimeInterval(
            start=ns_datetime(row.analysis_start_ns - BASELINE_NS),
            end=ns_datetime(row.analysis_start_ns),
        )
        if row.analysis_start_ns is not None
        else None,
        quality_flags=(
            ["rejected_records_present"] if row.data_quality.get("rejected_count") else []
        )
        + (["baseline_unrecorded"] if row.analysis_start_ns is None else []),
        last_investigation_status=latest,
        availability_recorded=bool(session.scalar(select(any_span)))
        and not bool(session.scalar(select(missing))),
    )


def episodes(
    resources: Resources, limit: int, cursor: str | None, origin: DataOrigin | None
) -> EpisodePage:
    scope = f"episodes:{DEFAULT_WORKSPACE_ID}:{origin}"
    boundary = decode_cursor(cursor, scope)
    query = select(EpisodeRow).where(EpisodeRow.workspace_id == DEFAULT_WORKSPACE_ID)
    if origin is not None:
        query = query.where(EpisodeRow.origin == origin.value)
    if boundary:
        query = query.where(tuple_(EpisodeRow.created_at, EpisodeRow.id) > boundary)
    with resources.factory() as session:
        rows = session.scalars(
            query.order_by(EpisodeRow.created_at, EpisodeRow.id).limit(limit + 1)
        ).all()
        selected = rows[:limit]
        return EpisodePage(
            items=[episode_view(session, row) for row in selected],
            next_cursor=encode_cursor(scope, selected[-1].created_at, selected[-1].id)
            if len(rows) > limit
            else None,
        )


def job_error(row: JobRow) -> PublicError | None:
    return (
        PublicError(
            code=row.safe_error,
            message="The job could not complete.",
            retryable=row.status == "RETRY_WAIT",
        )
        if row.safe_error
        else None
    )


def job_view(row: JobRow) -> JobView:
    return JobView(
        job_id=row.id,
        kind="investigate" if row.kind == "INVESTIGATE" else "import",
        status=JobStatus(row.status),
        phase=row.checkpoint,
        completed_units=int(row.status == "SUCCEEDED"),
        total_units=1,
        attempt_count=row.attempts,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested=row.cancel_requested,
        error=job_error(row),
        episode_id=row.episode_id,
        result_id=UUID(row.payload["result_id"]) if row.payload.get("result_id") else None,
    )


def scoped_job(session: Session, identifier: UUID) -> JobRow:
    row = session.get(JobRow, identifier)
    if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID or row.kind == "IMPORT_STAGE":
        raise ApiError(404, "not_found", "The resource was not found.")
    return row


def investigation(resources: Resources, identifier: UUID) -> InvestigationView:
    with resources.factory() as session:
        job = session.get(JobRow, identifier)
        if job is not None:
            if (
                job.workspace_id != DEFAULT_WORKSPACE_ID
                or job.kind != "INVESTIGATE"
                or job.episode_id is None
            ):
                raise ApiError(404, "not_found", "The resource was not found.")
            result = (
                resources.investigations.show(DEFAULT_WORKSPACE_ID, UUID(job.payload["result_id"]))
                if job.status == "SUCCEEDED"
                else None
            )
            return InvestigationView(
                investigation_id=identifier,
                episode_id=job.episode_id,
                snapshot_id=UUID(job.payload["request"]["snapshot_id"]),
                job_id=job.id,
                status=JobStatus(job.status),
                result=result,
                error=job_error(job),
                created_at=job.created_at,
            )
        row = session.get(InvestigationRow, identifier)
        if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID:
            raise ApiError(404, "not_found", "The resource was not found.")
        result = resources.investigations.show(DEFAULT_WORKSPACE_ID, identifier)
        return InvestigationView(
            investigation_id=identifier,
            episode_id=row.episode_id,
            snapshot_id=row.snapshot_id,
            job_id=None,
            status=JobStatus.SUCCEEDED,
            result=result,
            error=None,
            created_at=row.created_at,
        )


def investigations(
    resources: Resources, episode_id: UUID, limit: int, cursor: str | None
) -> InvestigationPage:
    scope = f"investigations:{DEFAULT_WORKSPACE_ID}:{episode_id}"
    boundary = decode_cursor(cursor, scope)
    query = select(InvestigationRow).where(
        InvestigationRow.workspace_id == DEFAULT_WORKSPACE_ID,
        InvestigationRow.episode_id == episode_id,
    )
    if boundary:
        query = query.where(tuple_(InvestigationRow.created_at, InvestigationRow.id) > boundary)
    with resources.factory() as session:
        scoped_episode(session, episode_id)
        rows = session.scalars(
            query.order_by(InvestigationRow.created_at, InvestigationRow.id).limit(limit + 1)
        ).all()
        selected = rows[:limit]
        return InvestigationPage(
            items=[
                InvestigationView(
                    investigation_id=row.id,
                    episode_id=row.episode_id,
                    snapshot_id=row.snapshot_id,
                    job_id=None,
                    status=JobStatus(row.status),
                    result=None,
                    error=None,
                    created_at=row.created_at,
                )
                for row in selected
            ],
            next_cursor=encode_cursor(scope, selected[-1].created_at, selected[-1].id)
            if len(rows) > limit
            else None,
        )


def graph(
    resources: Resources,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
    focus: str | None,
    hops: int,
) -> GraphResponse:
    if hops > resources.settings.graph_max_hops:
        raise ApiError(422, "graph_limit", "The graph hop limit is exceeded.")
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
        query = select(SpanRow.service_key).where(
            SpanRow.episode_id == episode_id,
            SpanRow.workspace_id == DEFAULT_WORKSPACE_ID,
            SpanRow.end_ns <= as_ns(as_of),
        )
        if mode is TemporalMode.AVAILABLE:
            query = query.where(SpanRow.available_at <= as_of)
        visible = set(session.scalars(query.distinct().limit(81)))
    raw = resources.reader.subgraph(
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=episode_id,
        snapshot_id=snapshot_id,
        as_of_ns=as_ns(as_of),
        mode=event_mode(mode),
    )
    nodes = [n for n in raw["services"] if n["service_key"] in visible]
    included = {n["service_key"] for n in nodes}
    if focus:
        if focus not in included:
            raise ApiError(404, "not_found", "The focus service is not visible at this cutoff.")
        included = {focus}
        for _ in range(hops):
            included |= {
                endpoint
                for edge in raw["calls"]
                if edge["caller_service_key"] in included or edge["callee_service_key"] in included
                for endpoint in (edge["caller_service_key"], edge["callee_service_key"])
            }
    nodes = [n for n in nodes if n["service_key"] in included]
    cap_nodes = resources.settings.graph_max_nodes
    truncated = len(nodes) > cap_nodes
    nodes = nodes[:cap_nodes]
    included = {n["service_key"] for n in nodes}
    edges = []
    for edge in raw["calls"]:
        caller, callee = edge["caller_service_key"], edge["callee_service_key"]
        if caller in included and callee in included:
            edges.append(
                GraphEdge(
                    edge_id=str(uuid5(snapshot_id, f"{caller}:{callee}")),
                    source_service_id=caller,
                    target_service_id=callee,
                    relationship="CALLS",
                    supporting_evidence_ids=[
                        f"observation:{oid}" for oid in edge["support_observation_ids"][:20]
                    ],
                )
            )
    truncated |= len(edges) > resources.settings.graph_max_edges
    return GraphResponse(
        episode_id=episode_id,
        snapshot_id=snapshot_id,
        as_of=as_of,
        temporal_mode=mode,
        nodes=[
            GraphNode(
                service_id=n["service_key"],
                service_key=n["service_key"],
                display_name=n["name"],
                quality_flags=[],
            )
            for n in nodes
        ],
        edges=edges[: resources.settings.graph_max_edges],
        truncated=truncated,
        warnings=["graph_truncated"] if truncated else [],
    )


def knowledge(
    resources: Resources, episode_id: UUID, snapshot_id: UUID, as_of: datetime, mode: TemporalMode
) -> KnowledgeResponse:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    with reader_lease(resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id):
        dependency_graph = graph(resources, episode_id, snapshot_id, as_of, mode, None, 3)
        observations = resources.reader.eligible_observation_summaries(
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_ns(as_of),
            mode=event_mode(mode),
        )
        chunks = resources.reader.eligible_document_chunks(
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_ns(as_of),
            mode=event_mode(mode),
            artifacts=resources.artifacts,
        )
        return assemble_knowledge(dependency_graph, observations, chunks)


def knowledge_checks(
    resources: Resources,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
    mode: TemporalMode,
    symptom: str,
) -> KnowledgeChecksResponse:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    with reader_lease(resources.factory, DEFAULT_WORKSPACE_ID, episode_id, snapshot_id):
        dependency_graph = graph(resources, episode_id, snapshot_id, as_of, mode, None, 3)
        if symptom not in {node.service_id for node in dependency_graph.nodes}:
            raise ApiError(404, "not_found", "The symptom service is not visible at this cutoff.")
        chunks = resources.reader.eligible_document_chunks(
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_ns(as_of),
            mode=event_mode(mode),
            artifacts=resources.artifacts,
        )
        return reachable_checks(dependency_graph, chunks, symptom)


def series(
    resources: Resources, episode_id: UUID, snapshot_id: UUID, as_of: datetime, mode: TemporalMode
) -> SeriesResponse:
    with resources.factory() as session:
        scoped_snapshot(session, episode_id, snapshot_id, as_of, mode)
    eligible = resources.reader.eligible_observation_summaries(
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=episode_id,
        snapshot_id=snapshot_id,
        as_of_ns=as_ns(as_of),
        mode=event_mode(mode),
    )
    ids = [UUID(row["observation_id"]) for row in eligible]
    with resources.factory() as session:
        # Replay materializes several source-verified versions of the same bucket.
        # Plot one intact eligible observation per service/interval, preferring
        # the greatest recorded coverage (including known late arrivals). Never
        # add their counts together or combine statistics across versions.
        versions = (
            select(
                ObservationRow.id,
                func.row_number()
                .over(
                    partition_by=(
                        ObservationRow.service_key,
                        ObservationRow.start_ns,
                        ObservationRow.end_ns,
                    ),
                    order_by=(
                        ObservationRow.sample_count.desc(),
                        ObservationRow.available_at.desc().nulls_last(),
                        ObservationRow.id,
                    ),
                )
                .label("position"),
            )
            .where(
                ObservationRow.id.in_(ids),
                ObservationRow.episode_id == episode_id,
                ObservationRow.observation_type == "service",
                ObservationRow.end_ns - ObservationRow.start_ns == 10_000_000_000,
            )
            .subquery()
        )
        rows = session.scalars(
            select(ObservationRow)
            .where(
                ObservationRow.id.in_(select(versions.c.id).where(versions.c.position == 1)),
            )
            .order_by(ObservationRow.end_ns, ObservationRow.service_key)
            .limit(2001)
        ).all()
        return SeriesResponse(
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of=as_of,
            temporal_mode=mode,
            truncated=len(rows) > 2000,
            points=[
                SeriesPoint(
                    service_id=row.service_key,
                    observation_id=row.id,
                    start_ns=str(row.start_ns),
                    end_ns=str(row.end_ns),
                    sample_count=row.sample_count,
                    error_count=row.error_count,
                    p50_ms=row.p50_ms,
                    p95_ms=row.p95_ms,
                )
                for row in rows[:2000]
                if row.service_key
            ],
        )


def evidence(
    resources: Resources, identifier: str, snapshot_id: UUID, as_of: datetime, mode: TemporalMode
) -> Evidence:
    with resources.factory() as session:
        snapshot = session.get(SnapshotRow, snapshot_id)
        if snapshot is None or snapshot.workspace_id != DEFAULT_WORKSPACE_ID:
            raise ApiError(404, "not_found", "The resource was not found.")
        scoped_snapshot(session, snapshot.episode_id, snapshot_id, as_of, mode)
    request = RetrievalRequest(
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=snapshot.episode_id,
        snapshot_id=snapshot_id,
        as_of_ns=as_ns(as_of),
        mode=event_mode(mode),
        question="inspect evidence",
    )
    selected = next(
        (
            c
            for c in corpus_from_reader(resources.reader, resources.artifacts, request)
            if c.evidence_id == identifier
        ),
        None,
    )
    if selected is None:
        raise ApiError(404, "not_found", "The evidence is not visible in this snapshot and time.")
    return evidence_from_candidate(
        selected, resources.reader, SpanLocatorResolver(resources.artifacts), mode=event_mode(mode)
    )


def evaluation(resources: Resources, identifier: UUID) -> EvaluationReport:
    with resources.factory() as session:
        row = session.scalar(
            select(EvaluationReportRow)
            .join(
                SourceArtifactRow,
                SourceArtifactRow.content_hash == EvaluationReportRow.artifact_hash,
            )
            .where(
                EvaluationReportRow.id == identifier,
                EvaluationReportRow.status == "PUBLISHED",
                SourceArtifactRow.workspace_id == DEFAULT_WORKSPACE_ID,
            )
        )
        if row is None:
            raise ApiError(404, "not_found", "The published report was not found.")
        report = EvaluationReport.model_validate_json(
            resources.artifacts.get_bytes(row.artifact_hash)
        )
        if report.report_id != identifier or report.published_at is None:
            raise ApiError(503, "report_unavailable", "The published report is unavailable.")
        return report


def evaluations(resources: Resources, limit: int, cursor: str | None) -> EvaluationPage:
    scope = f"evaluations:{DEFAULT_WORKSPACE_ID}"
    boundary = decode_cursor(cursor, scope)
    query = (
        select(EvaluationReportRow)
        .join(
            SourceArtifactRow, SourceArtifactRow.content_hash == EvaluationReportRow.artifact_hash
        )
        .where(
            EvaluationReportRow.status == "PUBLISHED",
            SourceArtifactRow.workspace_id == DEFAULT_WORKSPACE_ID,
        )
    )
    if boundary:
        query = query.where(
            tuple_(EvaluationReportRow.created_at, EvaluationReportRow.id) > boundary
        )
    with resources.factory() as session:
        rows = session.scalars(
            query.order_by(EvaluationReportRow.created_at, EvaluationReportRow.id).limit(limit + 1)
        ).all()
        selected = rows[:limit]
    return EvaluationPage(
        items=[evaluation(resources, row.id) for row in selected],
        next_cursor=encode_cursor(scope, selected[-1].created_at, selected[-1].id)
        if len(rows) > limit
        else None,
    )
