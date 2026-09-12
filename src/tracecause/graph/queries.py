"""Bounded graph reads with authoritative support and temporal validation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from neo4j import Driver, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.documents.linking import ServiceIdentity, resolve_mentions
from tracecause.documents.markdown import parse_document
from tracecause.documents.verification import verified_assertions
from tracecause.graph.errors import GraphReadError
from tracecause.graph.maintenance import reader_lease
from tracecause.graph.projection import _node_id
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.schema import (
    ChunkRow,
    DocumentRow,
    EpisodeRow,
    ObservationRow,
    ObservationSpanRow,
    SnapshotRow,
    SpanRow,
)

MAX_HOPS = 3
MAX_NODES = 80
MAX_EDGES = 120
MAX_PATHS = 3
MAX_TRAVERSAL_STATES = 500
MAX_DOCUMENT_CHUNKS = 1000
MAX_DOCUMENT_ASSERTIONS = 4000
MAX_RETRIEVAL_OBSERVATIONS = 19_000
MAX_RETRIEVAL_SUPPORTS = 128_000
MAX_RETRIEVAL_SOURCE_SPANS = 40_000
TOPOLOGY_LOOKBACK_NS = 360_000_000_000


@dataclass(frozen=True)
class EligibleCall:
    caller_service_key: str
    callee_service_key: str
    support_observation_ids: tuple[UUID, ...]


def _cutoff_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value // 1_000_000_000, UTC).replace(
        microsecond=(value % 1_000_000_000) // 1000
    )


class GraphReader:
    def __init__(self, session_factory: sessionmaker[Session], driver: Driver) -> None:
        self.session_factory = session_factory
        self.driver = driver

    def _validate_snapshot(
        self,
        session: Session,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"],
    ) -> None:
        if as_of_ns <= 0 or as_of_ns > 2**63 - 1 or mode not in {"event", "available"}:
            raise GraphReadError("invalid graph cutoff or temporal mode")
        episode = session.get(EpisodeRow, episode_id)
        snapshot = session.get(SnapshotRow, snapshot_id)
        if (
            episode is None
            or snapshot is None
            or episode.workspace_id != workspace_id
            or snapshot.workspace_id != workspace_id
            or snapshot.episode_id != episode_id
            or snapshot.status != "READY"
        ):
            raise GraphReadError("snapshot is missing, unpublished or outside scope")
        if mode == "available":
            missing = session.scalar(
                select(func.count())
                .select_from(SpanRow)
                .where(
                    SpanRow.episode_id == episode_id,
                    SpanRow.workspace_id == workspace_id,
                    SpanRow.end_ns <= as_of_ns,
                    SpanRow.available_at.is_(None),
                )
            )
            if missing:
                raise GraphReadError("reliable source availability is absent")
        with self.driver.session(database="neo4j") as graph:
            marker = graph.run(
                Query(
                    "MATCH (s:Snapshot {id:$sid,workspace_id:$wid,episode_id:$eid,complete:true}) "
                    "RETURN s.content_hash AS hash",
                    timeout=3.0,
                ),
                sid=str(snapshot_id),
                wid=str(workspace_id),
                eid=str(episode_id),
            ).single()
        if marker is None or marker["hash"] != snapshot.content_hash:
            raise GraphReadError("published graph projection is unavailable")

    def _graph_edges(
        self,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        focus: str | None,
        allow_truncation: bool = False,
    ) -> list[dict[str, Any]]:
        with self.driver.session(database="neo4j") as graph:
            records = graph.run(
                Query(
                    "MATCH (a:Service {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid})"
                    "-[r:CALLS {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}]->"
                    "(b:Service {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                    "WHERE $focus IS NULL OR a.service_key=$focus OR b.service_key=$focus "
                    "RETURN a.service_key AS caller,b.service_key AS callee,"
                    "r.support_observation_ids AS supports "
                    "ORDER BY caller,callee LIMIT $limit",
                    timeout=3.0,
                ),
                wid=str(workspace_id),
                eid=str(episode_id),
                sid=str(snapshot_id),
                focus=focus,
                limit=MAX_EDGES + 1,
            ).data()
        if len(records) > MAX_EDGES and not allow_truncation:
            raise GraphReadError("graph edge limit exceeded")
        return records

    def _eligible_edges(
        self,
        session: Session,
        records: list[dict[str, Any]],
        workspace_id: UUID,
        episode_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"],
    ) -> tuple[EligibleCall, ...]:
        raw_ids: set[UUID] = set()
        for record in records:
            supports = record.get("supports")
            if not isinstance(supports, list):
                continue
            for value in supports:
                if not isinstance(value, str):
                    continue
                try:
                    raw_ids.add(UUID(value))
                except ValueError:
                    continue
        if len(raw_ids) > 20_000:
            raise GraphReadError("graph support limit exceeded")
        observations = (
            session.scalars(
                select(ObservationRow).where(
                    ObservationRow.id.in_(raw_ids),
                    ObservationRow.episode_id == episode_id,
                    ObservationRow.end_ns <= as_of_ns,
                    ObservationRow.end_ns > as_of_ns - TOPOLOGY_LOOKBACK_NS,
                    ObservationRow.sample_count > 0,
                )
            ).all()
            if raw_ids
            else []
        )
        by_id = {item.id: item for item in observations}
        refs = (
            session.scalars(
                select(ObservationSpanRow).where(
                    ObservationSpanRow.observation_id.in_(by_id),
                    ObservationSpanRow.episode_id == episode_id,
                )
            ).all()
            if by_id
            else []
        )
        support_by_id: dict[UUID, list[ObservationSpanRow]] = defaultdict(list)
        source_keys = {(item.trace_id, item.span_id) for item in refs}
        if len(source_keys) > 20_000:
            raise GraphReadError("graph source support limit exceeded")
        # Fetch complete trace groups through the episode/trace index. A large
        # tuple-IN predicate on (trace_id, span_id) can exceed the database's
        # statement timeout even for a bounded set of observation supports.
        sources: list[SpanRow] = []
        trace_ids = sorted({trace_id for trace_id, _ in source_keys})
        for offset in range(0, len(trace_ids), 500):
            sources.extend(
                session.scalars(
                    select(SpanRow)
                    .where(
                        SpanRow.episode_id == episode_id,
                        SpanRow.workspace_id == workspace_id,
                        SpanRow.trace_id.in_(trace_ids[offset : offset + 500]),
                    )
                    .limit(20_001)
                ).all()
            )
            if len(sources) > 20_000:
                raise GraphReadError("graph source span limit exceeded")
        by_source = {
            (item.trace_id, item.span_id): item
            for item in sources
            if (item.trace_id, item.span_id) in source_keys
        }
        for ref in refs:
            support_by_id[ref.observation_id].append(ref)
        cutoff_time = _cutoff_ns(as_of_ns)
        eligible: list[EligibleCall] = []
        for record in records:
            caller, callee = record["caller"], record["callee"]
            if not isinstance(caller, str) or not isinstance(callee, str) or caller == callee:
                continue
            valid: list[UUID] = []
            for raw in record.get("supports") or []:
                try:
                    observation_id = UUID(raw)
                except (ValueError, TypeError, AttributeError):
                    continue
                obs = by_id.get(observation_id)
                if (
                    obs is None
                    or obs.observation_type != "dependency"
                    or obs.caller_service_key != caller
                    or obs.callee_service_key != callee
                    or obs.attributes.get("bucket") is not True
                    or mode == "available"
                    and (obs.available_at is None or obs.available_at > cutoff_time)
                ):
                    continue
                supports = support_by_id[observation_id]
                if not supports:
                    continue
                measured = []
                ancestors = []
                supported = True
                for ref in supports:
                    source = by_source.get((ref.trace_id, ref.span_id))
                    if (
                        source is None
                        or source.end_ns > as_of_ns
                        or mode == "available"
                        and (source.available_at is None or source.available_at > cutoff_time)
                    ):
                        supported = False
                        break
                    if ref.role == "measurement":
                        measured.append(source)
                    elif ref.role == "ancestor":
                        ancestors.append(source)
                if (
                    supported
                    and len(measured) == obs.sample_count
                    and all(
                        item.kind == "SERVER" and item.service_key == callee for item in measured
                    )
                    and any(item.service_key == caller for item in ancestors)
                ):
                    valid.append(observation_id)
            if valid:
                eligible.append(EligibleCall(caller, callee, tuple(sorted(set(valid), key=str))))
        return tuple(
            sorted(eligible, key=lambda item: (item.caller_service_key, item.callee_service_key))
        )

    def eligible_calls(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"] = "event",
        focus_service_key: str | None = None,
    ) -> tuple[EligibleCall, ...]:
        if focus_service_key is not None and (
            not focus_service_key or len(focus_service_key) > 200
        ):
            raise GraphReadError("invalid focus service key")
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            with self.session_factory() as session:
                self._validate_snapshot(
                    session, workspace_id, episode_id, snapshot_id, as_of_ns, mode
                )
                records = self._graph_edges(
                    workspace_id, episode_id, snapshot_id, focus_service_key
                )
                return self._eligible_edges(
                    session, records, workspace_id, episode_id, as_of_ns, mode
                )

    def eligible_calls_bounded(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"] = "event",
    ) -> tuple[tuple[EligibleCall, ...], bool]:
        """Return a deterministic edge prefix and disclose graph fan-out truncation."""
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            with self.session_factory() as session:
                self._validate_snapshot(
                    session, workspace_id, episode_id, snapshot_id, as_of_ns, mode
                )
                records = self._graph_edges(
                    workspace_id, episode_id, snapshot_id, None, allow_truncation=True
                )
                truncated = len(records) > MAX_EDGES
                return (
                    self._eligible_edges(
                        session, records[:MAX_EDGES], workspace_id, episode_id, as_of_ns, mode
                    ),
                    truncated,
                )

    def neighbors(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        service_key: str,
        mode: Literal["event", "available"] = "event",
    ) -> tuple[EligibleCall, ...]:
        if not service_key or len(service_key) > 200:
            raise GraphReadError("invalid service key")
        return self.eligible_calls(
            workspace_id=workspace_id,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_of_ns,
            mode=mode,
            focus_service_key=service_key,
        )

    def support_paths(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        source_service_key: str,
        target_service_key: str,
        max_hops: int = MAX_HOPS,
        mode: Literal["event", "available"] = "event",
    ) -> tuple[dict[str, Any], ...]:
        if (
            not 1 <= max_hops <= MAX_HOPS
            or not source_service_key
            or not target_service_key
            or len(source_service_key) > 200
            or len(target_service_key) > 200
            or source_service_key == target_service_key
        ):
            raise GraphReadError("invalid path endpoints or hop limit")
        edges = self.eligible_calls(
            workspace_id=workspace_id,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_of_ns,
            mode=mode,
        )
        outgoing: dict[str, list[EligibleCall]] = defaultdict(list)
        for edge in edges:
            outgoing[edge.caller_service_key].append(edge)
        pending: deque[tuple[str, tuple[str, ...], tuple[EligibleCall, ...]]] = deque(
            [(source_service_key, (source_service_key,), ())]
        )
        found: list[dict[str, Any]] = []
        examined = 0
        while pending and len(found) < MAX_PATHS:
            examined += 1
            if examined > MAX_TRAVERSAL_STATES or len(pending) > MAX_TRAVERSAL_STATES:
                raise GraphReadError("graph path traversal limit exceeded")
            current, nodes, steps = pending.popleft()
            if len(steps) >= max_hops:
                continue
            for edge in outgoing[current]:
                if edge.callee_service_key in nodes:
                    continue
                next_nodes = (*nodes, edge.callee_service_key)
                next_steps = (*steps, edge)
                if edge.callee_service_key == target_service_key:
                    found.append(
                        {
                            "service_keys": next_nodes,
                            "edges": [
                                {
                                    "caller_service_key": item.caller_service_key,
                                    "callee_service_key": item.callee_service_key,
                                    "support_observation_ids": [
                                        str(value) for value in item.support_observation_ids
                                    ],
                                }
                                for item in next_steps
                            ],
                        }
                    )
                    if len(found) == MAX_PATHS:
                        break
                else:
                    pending.append((edge.callee_service_key, next_nodes, next_steps))
        return tuple(found)

    def observation(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        observation_id: UUID,
        mode: Literal["event", "available"] = "event",
    ) -> dict[str, Any]:
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            return self._observation_with_lease(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=snapshot_id,
                as_of_ns=as_of_ns,
                observation_id=observation_id,
                mode=mode,
            )

    def _observation_with_lease(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        observation_id: UUID,
        mode: Literal["event", "available"],
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            self._validate_snapshot(session, workspace_id, episode_id, snapshot_id, as_of_ns, mode)
            row = session.get(ObservationRow, observation_id)
            if row is None or row.episode_id != episode_id or row.end_ns > as_of_ns:
                raise GraphReadError("observation is missing or after cutoff")
            cutoff_time = _cutoff_ns(as_of_ns)
            if mode == "available" and (row.available_at is None or row.available_at > cutoff_time):
                raise GraphReadError("observation is unavailable at cutoff")
            with self.driver.session(database="neo4j") as graph:
                present = graph.run(
                    Query(
                        "MATCH (n:Observation {id:$id,workspace_id:$wid,"
                        "episode_id:$eid,snapshot_id:$sid}) "
                        "RETURN n.source_id AS source_id",
                        timeout=3.0,
                    ),
                    id=_node_id(snapshot_id, "Observation", str(observation_id)),
                    wid=str(workspace_id),
                    eid=str(episode_id),
                    sid=str(snapshot_id),
                ).single()
            if present is None or present["source_id"] != str(observation_id):
                raise GraphReadError("observation is outside published projection")
            refs = session.scalars(
                select(ObservationSpanRow).where(
                    ObservationSpanRow.observation_id == observation_id,
                    ObservationSpanRow.episode_id == episode_id,
                )
            ).all()
            supports = []
            for ref in refs:
                source = session.get(SpanRow, (episode_id, ref.trace_id, ref.span_id))
                if (
                    source is None
                    or source.end_ns > as_of_ns
                    or (
                        mode == "available"
                        and (source.available_at is None or source.available_at > cutoff_time)
                    )
                ):
                    raise GraphReadError("observation support is unavailable at cutoff")
                supports.append(
                    {
                        "trace_id": ref.trace_id,
                        "span_id": ref.span_id,
                        "role": ref.role,
                        "artifact_hash": source.source_artifact_hash,
                        "source_record_offset": source.source_record_offset,
                    }
                )
            if row.sample_count > 0 and not supports:
                raise GraphReadError("observation lacks support")
            return {
                "observation_id": str(observation_id),
                "episode_id": str(episode_id),
                "snapshot_id": str(snapshot_id),
                "type": row.observation_type,
                "service_key": row.service_key,
                "caller_service_key": row.caller_service_key,
                "callee_service_key": row.callee_service_key,
                "start_ns": row.start_ns,
                "end_ns": row.end_ns,
                "sample_count": row.sample_count,
                "error_count": row.error_count,
                "p50_ms": row.p50_ms,
                "p95_ms": row.p95_ms,
                "available_at": row.available_at.isoformat() if row.available_at else None,
                "provenance_hash": row.provenance_hash,
                "supports": sorted(
                    supports, key=lambda item: (item["trace_id"], item["span_id"], item["role"])
                ),
            }

    def subgraph(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"] = "event",
    ) -> dict[str, Any]:
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            return self._subgraph_with_lease(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=snapshot_id,
                as_of_ns=as_of_ns,
                mode=mode,
            )

    def _subgraph_with_lease(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"],
    ) -> dict[str, Any]:
        edges = self.eligible_calls(
            workspace_id=workspace_id,
            episode_id=episode_id,
            snapshot_id=snapshot_id,
            as_of_ns=as_of_ns,
            mode=mode,
        )
        with self.driver.session(database="neo4j") as graph:
            nodes = graph.run(
                Query(
                    "MATCH (s:Service {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                    "RETURN s.service_key AS service_key,s.name AS name,"
                    "s.namespace AS namespace,s.environment AS environment LIMIT $limit",
                    timeout=3.0,
                ),
                wid=str(workspace_id),
                eid=str(episode_id),
                sid=str(snapshot_id),
                limit=MAX_NODES + 1,
            ).data()
        if len(nodes) > MAX_NODES:
            raise GraphReadError("graph node limit exceeded")
        return {
            "workspace_id": str(workspace_id),
            "episode_id": str(episode_id),
            "snapshot_id": str(snapshot_id),
            "as_of_ns": as_of_ns,
            "mode": mode,
            "services": sorted(nodes, key=lambda item: item["service_key"]),
            "calls": [
                {
                    "caller_service_key": edge.caller_service_key,
                    "callee_service_key": edge.callee_service_key,
                    "support_observation_ids": [
                        str(value) for value in edge.support_observation_ids
                    ],
                }
                for edge in edges
            ],
        }

    def eligible_observation_summaries(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        mode: Literal["event", "available"] = "event",
    ) -> tuple[dict[str, Any], ...]:
        """Expose the same provenance-checked observations to all text retrievers."""
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            with self.session_factory() as session:
                self._validate_snapshot(
                    session, workspace_id, episode_id, snapshot_id, as_of_ns, mode
                )
                with self.driver.session(database="neo4j") as graph:
                    records = graph.run(
                        Query(
                            "MATCH (o:Observation {workspace_id:$wid,episode_id:$eid,"
                            "snapshot_id:$sid}) WHERE o.end_ns <= $cutoff "
                            "RETURN o.source_id AS source_id,o.provenance_hash AS hash "
                            "LIMIT $limit",
                            timeout=3.0,
                        ),
                        wid=str(workspace_id),
                        eid=str(episode_id),
                        sid=str(snapshot_id),
                        cutoff=as_of_ns,
                        limit=MAX_RETRIEVAL_OBSERVATIONS + 1,
                    ).data()
                    services = graph.run(
                        Query(
                            "MATCH (s:Service {workspace_id:$wid,episode_id:$eid,"
                            "snapshot_id:$sid}) RETURN s.service_key AS key,s.name AS name,"
                            "s.namespace AS namespace,s.environment AS environment "
                            "LIMIT $limit",
                            timeout=3.0,
                        ),
                        wid=str(workspace_id),
                        eid=str(episode_id),
                        sid=str(snapshot_id),
                        limit=MAX_NODES + 1,
                    ).data()
                if len(records) > MAX_RETRIEVAL_OBSERVATIONS:
                    raise GraphReadError("retrieval observation limit exceeded")
                if len(services) > MAX_NODES:
                    raise GraphReadError("retrieval service limit exceeded")
                names = {
                    item["key"]: "/".join(
                        str(value)
                        for value in (item["namespace"], item["environment"], item["name"])
                        if value is not None
                    )
                    for item in services
                }
                try:
                    ids = {UUID(record["source_id"]) for record in records}
                except (TypeError, ValueError, KeyError) as exc:
                    raise GraphReadError("graph observation identity is invalid") from exc
                if len(ids) != len(records):
                    raise GraphReadError("duplicate graph observations")
                rows = (
                    session.scalars(
                        select(ObservationRow).where(
                            ObservationRow.id.in_(ids),
                            ObservationRow.episode_id == episode_id,
                            ObservationRow.end_ns <= as_of_ns,
                        )
                    ).all()
                    if ids
                    else []
                )
                by_id = {row.id: row for row in rows}
                refs = (
                    session.scalars(
                        select(ObservationSpanRow)
                        .where(
                            ObservationSpanRow.observation_id.in_(ids),
                            ObservationSpanRow.episode_id == episode_id,
                        )
                        .limit(MAX_RETRIEVAL_SUPPORTS + 1)
                    ).all()
                    if ids
                    else []
                )
                if len(refs) > MAX_RETRIEVAL_SUPPORTS:
                    raise GraphReadError("retrieval support limit exceeded")
                refs_by_id: dict[UUID, list[ObservationSpanRow]] = defaultdict(list)
                for ref in refs:
                    refs_by_id[ref.observation_id].append(ref)
                source_keys = sorted({(ref.trace_id, ref.span_id) for ref in refs})
                sources: list[SpanRow] = []
                # The span primary key starts with episode and trace. Fetching
                # bounded trace groups avoids expensive tuple-IN plans; the
                # exact span identities are still checked below for every ref.
                trace_ids = sorted({trace_id for trace_id, _ in source_keys})
                for offset in range(0, len(trace_ids), 500):
                    sources.extend(
                        session.scalars(
                            select(SpanRow)
                            .where(
                                SpanRow.workspace_id == workspace_id,
                                SpanRow.episode_id == episode_id,
                                SpanRow.trace_id.in_(trace_ids[offset : offset + 500]),
                            )
                            .limit(MAX_RETRIEVAL_SOURCE_SPANS + 1)
                        ).all()
                    )
                    if len(sources) > MAX_RETRIEVAL_SOURCE_SPANS:
                        raise GraphReadError("retrieval source span limit exceeded")
                by_source = {(row.trace_id, row.span_id): row for row in sources}
                cutoff_time = _cutoff_ns(as_of_ns)
                result: list[dict[str, Any]] = []
                for record in records:
                    observation_id = UUID(record["source_id"])
                    row = by_id.get(observation_id)
                    if row is None or row.provenance_hash != record["hash"]:
                        raise GraphReadError("graph observation differs from source")
                    if mode == "available" and (
                        row.available_at is None or row.available_at > cutoff_time
                    ):
                        continue
                    if row.sample_count <= 0:
                        continue
                    supports = []
                    eligible = True
                    measured: list[SpanRow] = []
                    ancestors: list[SpanRow] = []
                    for ref in refs_by_id[observation_id]:
                        source = by_source.get((ref.trace_id, ref.span_id))
                        if source is None:
                            raise GraphReadError("retrieval observation source is missing")
                        if source.end_ns > as_of_ns or (
                            mode == "available"
                            and (source.available_at is None or source.available_at > cutoff_time)
                        ):
                            eligible = False
                            break
                        supports.append(
                            {
                                "trace_id": ref.trace_id,
                                "span_id": ref.span_id,
                                "role": ref.role,
                                "artifact_hash": source.source_artifact_hash,
                                "source_record_offset": source.source_record_offset,
                            }
                        )
                        if ref.role == "measurement":
                            measured.append(source)
                        elif ref.role == "ancestor":
                            ancestors.append(source)
                        else:
                            raise GraphReadError("retrieval support role is invalid")
                    if not eligible:
                        continue
                    if len(measured) != row.sample_count or any(
                        item.kind != "SERVER"
                        or item.service_key
                        != (
                            row.service_key
                            if row.observation_type == "service"
                            else row.callee_service_key
                        )
                        for item in measured
                    ):
                        raise GraphReadError("retrieval observation support differs from source")
                    if row.observation_type == "dependency" and not any(
                        item.service_key == row.caller_service_key for item in ancestors
                    ):
                        raise GraphReadError("retrieval call lacks caller support")
                    subject = (
                        f"{names.get(row.service_key, row.service_key)} "
                        if row.service_key is not None
                        else f"{names.get(row.caller_service_key, row.caller_service_key)} "
                        f"calls {names.get(row.callee_service_key, row.callee_service_key)} "
                    )
                    result.append(
                        {
                            "observation_id": str(row.id),
                            "observation_type": row.observation_type,
                            "caller_service_key": row.caller_service_key,
                            "text": (
                                f"{row.observation_type} observation for {subject}; "
                                f"{row.sample_count} requests, {row.error_count} errors; "
                                f"p50 {row.p50_ms} ms, p95 {row.p95_ms} ms"
                            ),
                            "service_keys": sorted(
                                {
                                    key
                                    for key in (
                                        row.service_key,
                                        row.caller_service_key,
                                        row.callee_service_key,
                                    )
                                    if key is not None
                                }
                            ),
                            "start_ns": row.start_ns,
                            "end_ns": row.end_ns,
                            "available_at": row.available_at.isoformat()
                            if row.available_at
                            else None,
                            "provenance_hash": row.provenance_hash,
                            "supports": sorted(
                                supports,
                                key=lambda item: (item["trace_id"], item["span_id"], item["role"]),
                            ),
                        }
                    )
                return tuple(sorted(result, key=lambda item: item["observation_id"]))

    def eligible_document_chunks(
        self,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of_ns: int,
        artifacts: ArtifactRepository,
        mode: Literal["event", "available"] = "event",
    ) -> tuple[dict[str, Any], ...]:
        """Return exact eligible excerpts with PostgreSQL/artifact provenance."""
        with reader_lease(self.session_factory, workspace_id, episode_id, snapshot_id):
            with self.session_factory() as session:
                self._validate_snapshot(
                    session, workspace_id, episode_id, snapshot_id, as_of_ns, mode
                )
                with self.driver.session(database="neo4j") as graph:
                    records = graph.run(
                        Query(
                            "MATCH (d:Document {workspace_id:$wid,episode_id:$eid,"
                            "snapshot_id:$sid})-[r:HAS_CHUNK {workspace_id:$wid,"
                            "episode_id:$eid,snapshot_id:$sid}]->"
                            "(c:Chunk {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                            "WHERE d.published_at_ns <= $cutoff "
                            "RETURN d.document_id AS document_id,d.source_hash AS source_hash,"
                            "d.title AS title,d.license AS license,d.published_at_ns AS published,"
                            "c.chunk_id AS chunk_id,c.start_char AS start_char,"
                            "c.end_char AS end_char,c.normalized_text_hash AS normalized_hash,"
                            "c.text AS graph_text "
                            "LIMIT $limit",
                            timeout=3.0,
                        ),
                        wid=str(workspace_id),
                        eid=str(episode_id),
                        sid=str(snapshot_id),
                        cutoff=as_of_ns,
                        limit=MAX_DOCUMENT_CHUNKS + 1,
                    ).data()
                    mentions = graph.run(
                        Query(
                            "MATCH (c:Chunk {workspace_id:$wid,episode_id:$eid,"
                            "snapshot_id:$sid})-[m:MENTIONS {workspace_id:$wid,"
                            "episode_id:$eid,snapshot_id:$sid}]->"
                            "(s:Service {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                            "RETURN c.chunk_id AS chunk_id,collect(s.service_key) AS service_keys "
                            "LIMIT $limit",
                            timeout=3.0,
                        ),
                        wid=str(workspace_id),
                        eid=str(episode_id),
                        sid=str(snapshot_id),
                        limit=MAX_DOCUMENT_CHUNKS + 1,
                    ).data()
                    assertions = graph.run(
                        Query(
                            "MATCH (a:Assertion {workspace_id:$wid,episode_id:$eid,"
                            "snapshot_id:$sid}) "
                            "WHERE a.published_at_ns <= $cutoff "
                            "OPTIONAL MATCH (a)-[:ABOUT_SERVICE {workspace_id:$wid,"
                            "episode_id:$eid,snapshot_id:$sid}]->"
                            "(s:Service {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                            "OPTIONAL MATCH (a)-[:SUPPORTED_BY {workspace_id:$wid,"
                            "episode_id:$eid,snapshot_id:$sid}]->"
                            "(c:Chunk {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                            "RETURN a.assertion_id AS assertion_id,a.document_id AS document_id,"
                            "a.predicate AS predicate,a.quote AS quote,a.start_char AS start_char,"
                            "a.end_char AS end_char,a.extractor_version AS extractor_version,"
                            "a.verification_status AS verification_status,"
                            "collect(DISTINCT s.service_key) AS service_keys,"
                            "collect(DISTINCT c.chunk_id) AS chunk_ids LIMIT $limit",
                            timeout=3.0,
                        ),
                        wid=str(workspace_id),
                        eid=str(episode_id),
                        sid=str(snapshot_id),
                        cutoff=as_of_ns,
                        limit=MAX_DOCUMENT_ASSERTIONS + 1,
                    ).data()
                if len(assertions) > MAX_DOCUMENT_ASSERTIONS:
                    raise GraphReadError("document assertion limit exceeded")
                if len(records) > MAX_DOCUMENT_CHUNKS:
                    raise GraphReadError("document chunk limit exceeded")
                if len(mentions) > MAX_DOCUMENT_CHUNKS:
                    raise GraphReadError("document mention limit exceeded")
                service_keys_by_chunk = {
                    item["chunk_id"]: tuple(sorted(set(item["service_keys"]))) for item in mentions
                }
                identities = session.execute(
                    select(
                        SpanRow.service_key,
                        SpanRow.namespace,
                        SpanRow.environment,
                        SpanRow.service_name,
                    )
                    .where(
                        SpanRow.workspace_id == workspace_id,
                        SpanRow.episode_id == episode_id,
                        SpanRow.service_key.is_not(None),
                    )
                    .distinct()
                    .limit(MAX_NODES + 1)
                ).all()
                if len(identities) > MAX_NODES:
                    raise GraphReadError("document service identity limit exceeded")
                services = tuple(
                    ServiceIdentity(
                        key,
                        namespace or "<missing-namespace>",
                        environment or "<missing-environment>",
                        name or "",
                    )
                    for key, namespace, environment, name in identities
                    if key is not None
                )
                source_links: dict[UUID, Any] = {}
                source_assertions: dict[UUID, tuple[dict[str, Any], ...]] = {}
                cutoff_time = _cutoff_ns(as_of_ns)
                result: list[dict[str, Any]] = []
                for record in records:
                    try:
                        document_id = UUID(record["document_id"])
                        chunk_id = UUID(record["chunk_id"])
                    except (TypeError, ValueError, KeyError) as exc:
                        raise GraphReadError("graph document identity is invalid") from exc
                    document = session.get(DocumentRow, document_id)
                    chunk = session.get(ChunkRow, chunk_id)
                    if (
                        document is None
                        or chunk is None
                        or document.workspace_id != workspace_id
                        or chunk.document_id != document_id
                        or document.source_artifact_hash != record["source_hash"]
                        or document.published_at is None
                        or document.published_at > cutoff_time
                        or chunk.start_char != record["start_char"]
                        or chunk.end_char != record["end_char"]
                        or chunk.normalized_text_hash != record["normalized_hash"]
                    ):
                        raise GraphReadError("graph document differs from authoritative source")
                    if mode == "available" and document.available_at is None:
                        raise GraphReadError("reliable document availability is absent")
                    if (
                        mode == "available"
                        and document.available_at is not None
                        and (document.available_at > cutoff_time)
                    ):
                        continue
                    raw = artifacts.get_bytes(document.source_artifact_hash)
                    parsed = parse_document(raw)
                    if (
                        parsed.document_id != document_id
                        or parsed.header.license != record["license"]
                        or parsed.header.title != document.title
                    ):
                        raise GraphReadError("graph document metadata differs from source")
                    source_text = parsed.source_text
                    excerpt = source_text[chunk.start_char : chunk.end_char]
                    if (
                        not excerpt
                        or chunk.end_char > len(source_text)
                        or excerpt != record["graph_text"]
                    ):
                        raise GraphReadError("document chunk offset is invalid")
                    if document_id not in source_links:
                        source_links[document_id] = resolve_mentions(parsed, services)
                        source_assertions[document_id] = verified_assertions(
                            parsed,
                            source_links[document_id],
                            [a for a in assertions if a["document_id"] == str(document_id)],
                            [c for c in records if c["document_id"] == str(document_id)],
                        )
                    expected_services = tuple(
                        sorted(
                            {
                                link.service_key
                                for link in source_links[document_id]
                                if link.status == "accepted"
                                and link.service_key is not None
                                and chunk.start_char <= link.start_char
                                and link.end_char <= chunk.end_char
                            }
                        )
                    )
                    if service_keys_by_chunk.get(str(chunk_id), ()) != expected_services:
                        raise GraphReadError("graph document mentions differ from source")
                    result.append(
                        {
                            "document_id": str(document_id),
                            "chunk_id": str(chunk_id),
                            "source_id": document.source_id,
                            "source_hash": document.source_artifact_hash,
                            "title": document.title,
                            "license": record["license"],
                            "published_at": document.published_at.isoformat(),
                            "available_at": (
                                document.available_at.isoformat() if document.available_at else None
                            ),
                            "start_char": chunk.start_char,
                            "end_char": chunk.end_char,
                            "text": excerpt,
                            "service_keys": service_keys_by_chunk.get(str(chunk_id), ()),
                            "verified_assertions": [
                                item
                                for item in source_assertions[document_id]
                                if chunk.start_char <= item["start_char"]
                                and item["end_char"] <= chunk.end_char
                            ],
                        }
                    )
                return tuple(
                    sorted(result, key=lambda item: (item["source_id"], item["start_char"]))
                )
