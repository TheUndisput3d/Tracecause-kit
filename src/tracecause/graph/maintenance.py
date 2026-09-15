"""Graph reconciliation, bounded reader leases and retirement planning."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from neo4j import Driver
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tracecause.graph.errors import GraphReadError
from tracecause.storage.schema import (
    ArtifactReferenceRow,
    EpisodeRow,
    InvestigationRow,
    JobRow,
    ReaderLeaseRow,
    SnapshotRow,
)


@dataclass(frozen=True)
class RetirementPlan:
    snapshot_id: UUID
    episode_id: UUID
    active: bool
    live_reader_leases: int
    pending_jobs: int
    saved_investigations: int
    historical_snapshots: int
    graph_node_count: int
    referenced_artifact_hashes: tuple[str, ...]
    artifact_deletion: str
    eligible: bool
    dry_run: bool


def _retention_references(session: Session, snapshot_id: UUID) -> tuple[int, int, int]:
    jobs = (
        session.scalar(
            select(func.count())
            .select_from(JobRow)
            .where(
                JobRow.kind == "INVESTIGATE",
                JobRow.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
                JobRow.payload["request"]["snapshot_id"].as_string() == str(snapshot_id),
            )
        )
        or 0
    )
    saved = (
        session.scalar(
            select(func.count())
            .select_from(InvestigationRow)
            .where(
                InvestigationRow.snapshot_id == snapshot_id,
            )
        )
        or 0
    )
    historical = (
        session.scalar(
            select(func.count())
            .select_from(SnapshotRow)
            .where(
                SnapshotRow.status.in_(("BUILDING", "READY")),
                SnapshotRow.membership["history_snapshot_ids"].contains([str(snapshot_id)]),
            )
        )
        or 0
    )
    return jobs, saved, historical


@contextmanager
def reader_lease(
    session_factory: sessionmaker[Session],
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
    *,
    seconds: int = 30,
    renew: bool = False,
) -> Iterator[None]:
    if not 1 <= seconds <= 60:
        raise GraphReadError("invalid reader lease duration")
    lease_id = uuid4()
    with session_factory.begin() as session:
        snapshot = session.scalar(
            select(SnapshotRow).where(SnapshotRow.id == snapshot_id).with_for_update()
        )
        if (
            snapshot is None
            or snapshot.workspace_id != workspace_id
            or snapshot.episode_id != episode_id
            or snapshot.status != "READY"
        ):
            raise GraphReadError("snapshot is unavailable for a reader lease")
        session.add(
            ReaderLeaseRow(
                id=lease_id,
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=snapshot_id,
                owner=f"reader-{lease_id}",
                expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
            )
        )
    stop = threading.Event()
    lost = threading.Event()

    def refresh() -> None:
        while not stop.wait(seconds / 3):
            try:
                with session_factory.begin() as session:
                    lease = session.get(ReaderLeaseRow, lease_id)
                    if lease is None or lease.expires_at <= datetime.now(UTC):
                        lost.set()
                        return
                    lease.expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
            except SQLAlchemyError:
                lost.set()
                return

    thread = threading.Thread(target=refresh, daemon=True) if renew else None
    if thread is not None:
        thread.start()
    try:
        yield
        if renew:
            with session_factory() as session:
                lease = session.get(ReaderLeaseRow, lease_id)
                if lost.is_set() or lease is None or lease.expires_at <= datetime.now(UTC):
                    raise GraphReadError("snapshot reader lease expired before completion")
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=5)
        with session_factory.begin() as session:
            lease = session.get(ReaderLeaseRow, lease_id)
            if lease is not None:
                session.delete(lease)


def reconciliation_inventory(
    session_factory: sessionmaker[Session], driver: Driver
) -> dict[str, object]:
    with session_factory() as session:
        snapshots = session.scalars(select(SnapshotRow)).all()
        known = {str(item.id): item for item in snapshots}
    with driver.session(database="neo4j") as graph:
        markers = {
            row["id"]: row
            for row in graph.run(
                "MATCH (s:Snapshot) RETURN s.id AS id,s.complete AS complete,"
                "s.content_hash AS hash,s.service_count AS services,"
                "s.observation_count AS observations,s.incident_count AS incidents,"
                "s.call_count AS calls,s.document_count AS documents,"
                "s.chunk_count AS chunks,s.assertion_count AS assertions"
            ).data()
        }
        for snapshot_id, marker in markers.items():
            counts = graph.run(
                "MATCH (s:Snapshot {id:$sid}) "
                "OPTIONAL MATCH (s)-[:CONTAINS {snapshot_id:$sid}]->(n) "
                "RETURN count(DISTINCT CASE WHEN n:Service THEN n END) AS services,"
                "count(DISTINCT CASE WHEN n:Observation THEN n END) AS observations,"
                "count(DISTINCT CASE WHEN n:Incident THEN n END) AS incidents,"
                "count(DISTINCT CASE WHEN n:Document THEN n END) AS documents,"
                "count(DISTINCT CASE WHEN n:Chunk THEN n END) AS chunks,"
                "count(DISTINCT CASE WHEN n:Assertion THEN n END) AS assertions",
                sid=snapshot_id,
            ).single(strict=True)
            calls = graph.run(
                "MATCH (:Service)-[r:CALLS {snapshot_id:$sid}]->(:Service) "
                "RETURN count(r) AS total",
                sid=snapshot_id,
            ).single(strict=True)["total"]
            marker["counts_valid"] = (
                counts is not None
                and all(
                    counts[name] == marker[name]
                    for name in (
                        "services",
                        "observations",
                        "incidents",
                        "documents",
                        "chunks",
                        "assertions",
                    )
                )
                and calls == marker["calls"]
            )
    stale_boundary = datetime.now(UTC) - timedelta(minutes=5)
    return {
        "stale_building_snapshot_ids": sorted(
            str(item.id)
            for item in snapshots
            if item.status == "BUILDING" and item.created_at < stale_boundary
        ),
        "ready_missing_or_invalid_projection_ids": sorted(
            str(item.id)
            for item in snapshots
            if item.status == "READY"
            and (
                str(item.id) not in markers
                or markers[str(item.id)]["complete"] is not True
                or markers[str(item.id)]["hash"] != item.content_hash
                or markers[str(item.id)]["counts_valid"] is not True
            )
        ),
        "orphan_graph_snapshot_ids": sorted(set(markers) - set(known)),
        "failed_partial_graph_snapshot_ids": sorted(
            str(item.id)
            for item in snapshots
            if item.status == "FAILED" and str(item.id) in markers
        ),
    }


def retirement_plan(
    session_factory: sessionmaker[Session],
    driver: Driver,
    *,
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
) -> RetirementPlan:
    with session_factory() as session:
        snapshot = session.get(SnapshotRow, snapshot_id)
        episode = session.get(EpisodeRow, episode_id)
        if (
            snapshot is None
            or episode is None
            or snapshot.workspace_id != workspace_id
            or episode.workspace_id != workspace_id
            or snapshot.episode_id != episode_id
            or snapshot.status not in {"READY", "FAILED", "TOMBSTONED"}
        ):
            raise GraphReadError("snapshot is missing or outside retirement scope")
        active = episode.active_snapshot_id == snapshot_id
        jobs, saved, historical = _retention_references(session, snapshot_id)
        leases = (
            session.scalar(
                select(func.count())
                .select_from(ReaderLeaseRow)
                .where(
                    ReaderLeaseRow.snapshot_id == snapshot_id,
                    ReaderLeaseRow.expires_at > datetime.now(UTC),
                )
            )
            or 0
        )
        hashes = tuple(
            sorted(
                set(
                    session.scalars(
                        select(ArtifactReferenceRow.artifact_hash).where(
                            ArtifactReferenceRow.episode_id == episode_id
                        )
                    ).all()
                )
            )
        )
    with driver.session(database="neo4j") as graph:
        count = graph.run(
            "MATCH (n {snapshot_id:$sid}) RETURN count(n) AS total",
            sid=str(snapshot_id),
        ).single(strict=True)["total"]
    return RetirementPlan(
        snapshot_id,
        episode_id,
        active,
        leases,
        jobs,
        saved,
        historical,
        count,
        hashes,
        "none; episode/document artifacts remain referenced",
        not active and leases == 0 and jobs == 0 and saved == 0 and historical == 0,
        True,
    )


def retire_snapshot(
    session_factory: sessionmaker[Session],
    driver: Driver,
    *,
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
    apply: bool = False,
) -> RetirementPlan:
    plan = retirement_plan(
        session_factory,
        driver,
        workspace_id=workspace_id,
        episode_id=episode_id,
        snapshot_id=snapshot_id,
    )
    if not apply:
        return plan
    with session_factory.begin() as session:
        snapshot = session.scalar(
            select(SnapshotRow).where(SnapshotRow.id == snapshot_id).with_for_update()
        )
        episode = session.scalar(
            select(EpisodeRow).where(EpisodeRow.id == episode_id).with_for_update()
        )
        if (
            snapshot is None
            or episode is None
            or snapshot.workspace_id != workspace_id
            or snapshot.episode_id != episode_id
            or episode.workspace_id != workspace_id
            or episode.active_snapshot_id == snapshot_id
        ):
            raise GraphReadError("active or foreign snapshot cannot be retired")
        live = session.scalar(
            select(func.count())
            .select_from(ReaderLeaseRow)
            .where(
                ReaderLeaseRow.snapshot_id == snapshot_id,
                ReaderLeaseRow.expires_at > datetime.now(UTC),
            )
        )
        if live:
            raise GraphReadError("snapshot has active reader leases")
        jobs, saved, historical = _retention_references(session, snapshot_id)
        if jobs or saved or historical:
            raise GraphReadError(
                "snapshot has pending jobs, investigations or historical references"
            )
        snapshot.status = "TOMBSTONED"
    with driver.session(database="neo4j") as graph:
        graph.run(
            "MATCH (n {snapshot_id:$sid}) DETACH DELETE n",
            sid=str(snapshot_id),
        ).consume()
    return RetirementPlan(
        plan.snapshot_id,
        plan.episode_id,
        False,
        0,
        0,
        0,
        0,
        0,
        plan.referenced_artifact_hashes,
        plan.artifact_deletion,
        True,
        False,
    )
