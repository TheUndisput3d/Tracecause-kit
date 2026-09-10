"""Rebuildable, provenance-validated Neo4j snapshot projection."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from neo4j import Driver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.contracts.canonical import semantic_sha256
from tracecause.documents.assertions import EXTRACTOR_VERSION
from tracecause.documents.linking import ALIAS_VERSION, ServiceIdentity
from tracecause.documents.markdown import CHUNKING_VERSION, TOKENIZER_REVISION
from tracecause.documents.projection import DocumentMaterial, load_document_material
from tracecause.graph.schema import GRAPH_SCHEMA_VERSION, audit_graph_schema, migrate_graph
from tracecause.operational import projection as operational_projection
from tracecause.operational import storage as operational_storage
from tracecause.operational.handoffs import (
    HANDOFF_VERSION,
    HandoffRecord,
    load_handoffs,
    project_handoffs,
    verify_handoffs,
)
from tracecause.operational.projection import OperationalMaterial
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import StorageConflict
from tracecause.storage.schema import (
    EpisodeRow,
    ObservationRow,
    ObservationSpanRow,
    SnapshotRow,
    SpanRow,
)


class GraphBuildError(ValueError):
    """A graph build cannot safely publish its projection."""


@dataclass(frozen=True)
class GraphMaterial:
    workspace_id: UUID
    episode_id: UUID
    content_hash: str
    services: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    incidents: tuple[dict[str, Any], ...]
    calls: tuple[dict[str, Any], ...]
    observation_links: tuple[dict[str, str], ...]
    incident_links: tuple[dict[str, str], ...]
    document_cutoff_ns: int
    document_material: DocumentMaterial
    operational_material: OperationalMaterial = OperationalMaterial()
    operational_hashes: tuple[str, ...] = ()
    history_snapshot_ids: tuple[UUID, ...] = ()
    handoffs: tuple[HandoffRecord, ...] = ()
    operational_replay_version: str | None = None
    document_scope_version: str | None = None
    operational_replay_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotBuild:
    snapshot_id: UUID
    episode_id: UUID
    content_hash: str
    services: int
    observations: int
    incidents: int
    calls: int
    status: str
    documents: int = 0
    chunks: int = 0
    assertions: int = 0


def _node_id(snapshot_id: UUID, label: str, source_key: str) -> str:
    return hashlib.sha256(f"{snapshot_id}:{label}:{source_key}".encode()).hexdigest()


def _as_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    utc = value.astimezone(UTC)
    return int(utc.timestamp()) * 1_000_000_000 + utc.microsecond * 1000


def load_graph_material(
    session: Session,
    workspace_id: UUID,
    episode_id: UUID,
    *,
    artifacts: ArtifactRepository | None = None,
    document_cutoff_ns: int | None = None,
    membership: dict[str, Any] | None = None,
) -> GraphMaterial:
    episode = session.get(EpisodeRow, episode_id)
    if (
        episode is None
        or episode.workspace_id != workspace_id
        or episode.ingestion_status != "IMPORTED"
    ):
        raise StorageConflict("episode is missing, out of scope or not imported")
    spans = session.scalars(
        select(SpanRow).where(
            SpanRow.episode_id == episode_id, SpanRow.workspace_id == workspace_id
        )
    ).all()
    stored_observations = session.scalars(
        select(ObservationRow).where(ObservationRow.episode_id == episode_id)
    ).all()
    # Keep historical snapshots replayable, but do not republish derived signals
    # whose healthy baseline was superseded by a repaired episode boundary.
    observations = [
        row
        for row in stored_observations
        if not isinstance(row.attributes.get("signal"), dict)
        or row.attributes["signal"].get("baseline_end_ns") == episode.analysis_start_ns
    ]
    if membership is not None:
        if membership.get("version") != 1:
            raise GraphBuildError("unsupported snapshot membership version")
        wanted = {UUID(value) for value in membership["observation_ids"]}
        observations = [row for row in stored_observations if row.id in wanted]
        if {row.id for row in observations} != wanted:
            raise GraphBuildError("snapshot observation membership is unavailable")
        document_cutoff_ns = membership["document_cutoff_ns"]
    refs = session.scalars(
        select(ObservationSpanRow).where(ObservationSpanRow.episode_id == episode_id)
    ).all()
    by_span = {(row.trace_id, row.span_id): row for row in spans}
    refs_by_observation: dict[UUID, list[ObservationSpanRow]] = defaultdict(list)
    for ref in refs:
        if (ref.trace_id, ref.span_id) not in by_span:
            raise GraphBuildError("observation support span is missing")
        refs_by_observation[ref.observation_id].append(ref)
    service_metadata: dict[str, tuple[str | None, str | None, str | None]] = {}
    for span in spans:
        if span.service_key is None:
            continue
        metadata = (span.namespace, span.environment, span.service_name)
        previous = service_metadata.get(span.service_key)
        if previous is not None and previous != metadata:
            raise GraphBuildError("service identity metadata conflicts")
        service_metadata[span.service_key] = metadata
    for obs in observations:
        for key in (obs.service_key, obs.caller_service_key, obs.callee_service_key):
            if key is not None and key not in service_metadata:
                raise GraphBuildError("observation references unknown service")
        supports = refs_by_observation[obs.id]
        if obs.sample_count > 0 and not supports:
            raise GraphBuildError("nonempty observation lacks span support")
        if obs.observation_type == "service":
            if obs.service_key is None:
                raise GraphBuildError("service observation lacks service")
            measured = [
                by_span[(ref.trace_id, ref.span_id)]
                for ref in supports
                if ref.role == "measurement"
            ]
            if len(measured) != obs.sample_count or any(
                item.kind != "SERVER" or item.service_key != obs.service_key for item in measured
            ):
                raise GraphBuildError("service observation support is invalid")
        elif obs.observation_type == "dependency":
            if (
                obs.caller_service_key is None
                or obs.callee_service_key is None
                or obs.caller_service_key == obs.callee_service_key
            ):
                raise GraphBuildError("dependency endpoints are invalid")
            measured = [
                by_span[(ref.trace_id, ref.span_id)]
                for ref in supports
                if ref.role == "measurement"
            ]
            ancestor = [
                by_span[(ref.trace_id, ref.span_id)] for ref in supports if ref.role == "ancestor"
            ]
            if (
                len(measured) != obs.sample_count
                or any(
                    item.kind != "SERVER" or item.service_key != obs.callee_service_key
                    for item in measured
                )
                or (
                    obs.sample_count > 0
                    and not any(item.service_key == obs.caller_service_key for item in ancestor)
                )
            ):
                raise GraphBuildError("dependency support is invalid")
        else:
            raise GraphBuildError("unknown observation type")
    services = tuple(
        {
            "source_key": key,
            "service_key": key,
            "namespace": service_metadata[key][0],
            "environment": service_metadata[key][1],
            "name": service_metadata[key][2],
        }
        for key in sorted(service_metadata)
    )
    graph_observations: list[dict[str, Any]] = []
    calls: dict[tuple[str, str], set[str]] = defaultdict(set)
    observation_links: list[dict[str, str]] = []
    incidents: list[dict[str, Any]] = []
    incident_links: list[dict[str, str]] = []
    for obs in sorted(observations, key=lambda item: str(item.id)):
        item_id = str(obs.id)
        graph_observations.append(
            {
                "source_id": item_id,
                "observation_type": obs.observation_type,
                "service_key": obs.service_key,
                "caller_service_key": obs.caller_service_key,
                "callee_service_key": obs.callee_service_key,
                "start_ns": obs.start_ns,
                "end_ns": obs.end_ns,
                "sample_count": obs.sample_count,
                "error_count": obs.error_count,
                "p50_ms": obs.p50_ms,
                "p95_ms": obs.p95_ms,
                "rule_version": obs.rule_version,
                "provenance_hash": obs.provenance_hash,
                "available_at_ns": _as_ns(obs.available_at),
                "support_count": len(refs_by_observation[obs.id]),
            }
        )
        if obs.observation_type == "service" and obs.service_key is not None:
            observation_links.append(
                {"observation_id": item_id, "service_key": obs.service_key, "kind": "ABOUT_SERVICE"}
            )
        elif obs.observation_type == "dependency":
            assert obs.caller_service_key is not None and obs.callee_service_key is not None
            observation_links.extend(
                (
                    {
                        "observation_id": item_id,
                        "service_key": obs.caller_service_key,
                        "kind": "FROM_SERVICE",
                    },
                    {
                        "observation_id": item_id,
                        "service_key": obs.callee_service_key,
                        "kind": "TO_SERVICE",
                    },
                )
            )
            if obs.sample_count > 0 and obs.attributes.get("bucket") is True:
                calls[(obs.caller_service_key, obs.callee_service_key)].add(item_id)
        signal = obs.attributes.get("signal")
        if obs.observation_type == "service" and isinstance(signal, dict):
            symptom = any(
                isinstance(signal.get(name), (int, float))
                and (abs(signal[name]) >= 3 if name == "volume_deviation_z" else signal[name] >= 3)
                for name in ("latency_deviation_z", "error_excess_z", "volume_deviation_z")
            )
            incidents.append(
                {
                    "source_id": item_id,
                    "service_key": obs.service_key,
                    "start_ns": signal.get("baseline_end_ns", obs.start_ns),
                    "end_ns": obs.end_ns,
                    "alert_provenance_observation_id": item_id,
                    "symptom_observed": symptom,
                    "available_at_ns": _as_ns(obs.available_at),
                }
            )
            if symptom and obs.service_key is not None:
                incident_links.append({"incident_id": item_id, "service_key": obs.service_key})
    call_rows = tuple(
        {
            "caller_service_key": caller,
            "callee_service_key": callee,
            "support_observation_ids": sorted(support_ids),
        }
        for (caller, callee), support_ids in sorted(calls.items())
    )
    effective_cutoff = (
        _as_ns(episode.event_end) if document_cutoff_ns is None else document_cutoff_ns
    )
    if effective_cutoff is None or effective_cutoff <= 0 or effective_cutoff > 2**63 - 1:
        raise GraphBuildError("invalid document cutoff")
    identities = tuple(
        ServiceIdentity(
            service_key=cast(str, row["service_key"]),
            namespace=row["namespace"] or "<missing-namespace>",
            environment=row["environment"] or "<missing-environment>",
            name=row["name"] or "",
        )
        for row in services
    )
    operational_hashes = (
        tuple(membership.get("operational_hashes", []))
        if membership is not None
        else operational_storage.source_hashes(session, workspace_id, episode_id)
    )
    operational_bundle = operational_storage.load_bundle(
        session, artifacts, workspace_id, episode_id, operational_hashes
    )
    operational_material = operational_projection.assemble(operational_bundle, workspace_id)
    history_ids = operational_storage.history_membership(
        session, workspace_id, episode, operational_bundle, membership
    )
    document_scope_version = (
        membership.get("document_scope_version")
        if membership is not None
        else "explicit-service-scope-v1"
        if operational_bundle is not None
        else None
    )
    document_material = load_document_material(
        session,
        artifacts,
        workspace_id,
        identities,
        effective_cutoff,
        tuple(UUID(value) for value in membership["document_ids"])
        if membership is not None
        else None,
        require_explicit_scope=document_scope_version == "explicit-service-scope-v1",
    )
    replay_version = (
        (
            membership.get("operational_replay_version")
            if membership is not None
            else episode.data_quality.get("operational_replay_version")
        )
        if operational_bundle is not None
        else None
    )
    replay_modes = (
        tuple(
            (membership if membership is not None else episode.data_quality).get(
                "operational_replay_modes", []
            )
        )
        if operational_bundle is not None
        else ()
    )
    if replay_modes not in {(), ("event",), ("event", "available")}:
        raise GraphBuildError("invalid operational replay modes")
    handoffs = (
        load_handoffs(
            session,
            artifacts,
            workspace_id,
            episode_id,
            _as_ns(episode.event_end) or effective_cutoff,
        )
        if operational_bundle is not None and artifacts is not None
        else ()
    )
    content_hash = semantic_sha256(
        {
            **(
                {
                    **({"operational_replay_version": replay_version} if replay_version else {}),
                    **({"operational_replay_modes": replay_modes} if replay_modes else {}),
                    **(
                        {"document_scope_version": document_scope_version}
                        if document_scope_version
                        else {}
                    ),
                    "operational_version": "1.0",
                    "handoff_version": HANDOFF_VERSION,
                    "operational_hashes": operational_hashes,
                    "history_snapshot_ids": [str(value) for value in history_ids],
                }
                if operational_hashes
                else {}
            ),
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "workspace_id": str(workspace_id),
            "episode_id": str(episode_id),
            "manifest_hash": episode.manifest_hash,
            "spans": sorted((row.trace_id, row.span_id, row.normalized_hash) for row in spans),
            "observations": sorted((str(row.id), row.provenance_hash) for row in observations),
            "document_cutoff_ns": effective_cutoff,
            "document_versions": {
                "tokenizer_revision": TOKENIZER_REVISION,
                "chunking_version": CHUNKING_VERSION,
                "alias_version": ALIAS_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
            },
            "documents": [
                (row["document_id"], row["source_hash"]) for row in document_material.documents
            ],
            "chunks": [
                (row["chunk_id"], row["normalized_text_hash"]) for row in document_material.chunks
            ],
            "assertions": [row["assertion_id"] for row in document_material.assertions],
            "document_links": sorted(
                (row["document_id"], row["chunk_id"])
                for row in document_material.document_chunk_links
            ),
            "mentions": sorted(
                (row["chunk_id"], row["service_key"])
                for row in document_material.chunk_service_links
            ),
            "assertion_support": sorted(
                (row["assertion_id"], row["chunk_id"])
                for row in document_material.assertion_chunk_links
            ),
            "assertion_subjects": sorted(
                (row["assertion_id"], row["service_key"])
                for row in document_material.assertion_service_links
            ),
        }
    )
    return GraphMaterial(
        workspace_id,
        episode_id,
        content_hash,
        services,
        tuple(graph_observations),
        tuple(incidents),
        call_rows,
        tuple(observation_links),
        tuple(incident_links),
        effective_cutoff,
        document_material,
        operational_material,
        operational_hashes,
        history_ids,
        handoffs,
        replay_version,
        document_scope_version,
        replay_modes,
    )


def _batch(items: tuple[dict[str, Any], ...], size: int = 200) -> list[list[dict[str, Any]]]:
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


def snapshot_membership(material: GraphMaterial) -> dict[str, Any]:
    return {
        **(
            {
                **(
                    {"operational_replay_version": material.operational_replay_version}
                    if material.operational_replay_version
                    else {}
                ),
                **(
                    {"document_scope_version": material.document_scope_version}
                    if material.document_scope_version
                    else {}
                ),
                **(
                    {"operational_replay_modes": list(material.operational_replay_modes)}
                    if material.operational_replay_modes
                    else {}
                ),
                "operational_hashes": list(material.operational_hashes),
                "history_snapshot_ids": [str(value) for value in material.history_snapshot_ids],
            }
            if material.operational_hashes
            else {}
        ),
        "version": 1,
        "observation_ids": sorted(row["source_id"] for row in material.observations),
        "document_ids": sorted(row["document_id"] for row in material.document_material.documents),
        "document_cutoff_ns": material.document_cutoff_ns,
    }


def project_material(
    driver: Driver,
    snapshot_id: UUID,
    material: GraphMaterial,
    *,
    fail_after: Literal["marker", "services", "observations", "edges"] | None = None,
) -> None:
    """Write only under a new snapshot ID; failure leaves a hidden partial build."""
    wid, eid, sid = str(material.workspace_id), str(material.episode_id), str(snapshot_id)
    with driver.session(database="neo4j") as session:
        session.run(
            "MERGE (snap:Snapshot {id:$sid}) "
            "SET snap.workspace_id=$wid,snap.episode_id=$eid,snap.snapshot_id=$sid,"
            "snap.schema_version=$version,snap.content_hash=$hash,snap.complete=false,"
            "snap.service_count=$services,snap.observation_count=$observations,"
            "snap.incident_count=$incidents,snap.call_count=$calls,"
            "snap.document_count=$documents,snap.chunk_count=$chunks,"
            "snap.assertion_count=$assertions,snap.document_cutoff_ns=$doc_cutoff",
            sid=sid,
            wid=wid,
            eid=eid,
            version=GRAPH_SCHEMA_VERSION,
            hash=material.content_hash,
            services=len(material.services),
            observations=len(material.observations),
            incidents=len(material.incidents),
            calls=len(material.calls),
            documents=len(material.document_material.documents),
            chunks=len(material.document_material.chunks),
            assertions=len(material.document_material.assertions),
            doc_cutoff=material.document_cutoff_ns,
        ).consume()
        if fail_after == "marker":
            raise GraphBuildError("injected_graph_build_failure")
        service_rows = tuple(
            {**row, "id": _node_id(snapshot_id, "Service", row["service_key"])}
            for row in material.services
        )
        for rows in _batch(service_rows):
            session.run(
                "MATCH (snap:Snapshot {id:$sid}) UNWIND $rows AS row "
                "MERGE (n:Service {id:row.id}) SET n += row, n.workspace_id=$wid,"
                "n.episode_id=$eid,n.snapshot_id=$sid "
                "MERGE (snap)-[:CONTAINS {workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(n)",
                sid=sid,
                wid=wid,
                eid=eid,
                rows=rows,
            ).consume()
        if fail_after == "services":
            raise GraphBuildError("injected_graph_build_failure")
        observation_rows = tuple(
            {**row, "id": _node_id(snapshot_id, "Observation", row["source_id"])}
            for row in material.observations
        )
        for rows in _batch(observation_rows):
            session.run(
                "MATCH (snap:Snapshot {id:$sid}) UNWIND $rows AS row "
                "MERGE (n:Observation:Retrievable {id:row.id}) SET n += row, n.workspace_id=$wid,"
                "n.episode_id=$eid,n.snapshot_id=$sid "
                "MERGE (snap)-[:CONTAINS {workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(n)",
                sid=sid,
                wid=wid,
                eid=eid,
                rows=rows,
            ).consume()
        for rows in _batch(material.observation_links):
            for kind in ("ABOUT_SERVICE", "FROM_SERVICE", "TO_SERVICE"):
                selected = [row for row in rows if row["kind"] == kind]
                if not selected:
                    continue
                session.run(
                    f"UNWIND $rows AS row MATCH (o:Observation {{id:row.observation_id}}) "
                    f"MATCH (s:Service {{id:row.service_id}}) "
                    f"MERGE (o)-[:{kind} {{workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid}]->(s)",
                    rows=[
                        {
                            "observation_id": _node_id(
                                snapshot_id, "Observation", row["observation_id"]
                            ),
                            "service_id": _node_id(snapshot_id, "Service", row["service_key"]),
                        }
                        for row in selected
                    ],
                    wid=wid,
                    eid=eid,
                    sid=sid,
                ).consume()
        if fail_after == "observations":
            raise GraphBuildError("injected_graph_build_failure")
        incident_rows = tuple(
            {**row, "id": _node_id(snapshot_id, "Incident", row["source_id"])}
            for row in material.incidents
        )
        for rows in _batch(incident_rows):
            session.run(
                "MATCH (snap:Snapshot {id:$sid}) UNWIND $rows AS row "
                "MERGE (n:Incident {id:row.id}) SET n += row, n.workspace_id=$wid,"
                "n.episode_id=$eid,n.snapshot_id=$sid "
                "MERGE (snap)-[:CONTAINS {workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(n)",
                sid=sid,
                wid=wid,
                eid=eid,
                rows=rows,
            ).consume()
        for rows in _batch(material.incident_links):
            session.run(
                "UNWIND $rows AS row MATCH (i:Incident {id:row.incident_id}) "
                "MATCH (s:Service {id:row.service_id}) "
                "MERGE (i)-[:HAS_SYMPTOM_ON {workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(s)",
                rows=[
                    {
                        "incident_id": _node_id(snapshot_id, "Incident", row["incident_id"]),
                        "service_id": _node_id(snapshot_id, "Service", row["service_key"]),
                    }
                    for row in rows
                ],
                wid=wid,
                eid=eid,
                sid=sid,
            ).consume()
        for rows in _batch(material.calls):
            session.run(
                "UNWIND $rows AS row MATCH (a:Service {id:row.caller_id}) "
                "MATCH (b:Service {id:row.callee_id}) "
                "MERGE (a)-[r:CALLS {workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}]->(b) "
                "SET r.support_observation_ids=row.support_observation_ids",
                rows=[
                    {
                        "caller_id": _node_id(snapshot_id, "Service", row["caller_service_key"]),
                        "callee_id": _node_id(snapshot_id, "Service", row["callee_service_key"]),
                        "support_observation_ids": row["support_observation_ids"],
                    }
                    for row in rows
                ],
                wid=wid,
                sid=sid,
                eid=eid,
            ).consume()
        document_material = material.document_material
        for label, source_field, items in (
            ("Document", "document_id", document_material.documents),
            ("Chunk", "chunk_id", document_material.chunks),
            ("Assertion", "assertion_id", document_material.assertions),
        ):
            projected = tuple(
                {**item, "id": _node_id(snapshot_id, label, item[source_field])} for item in items
            )
            for rows in _batch(projected):
                session.run(
                    f"MATCH (snap:Snapshot {{id:$sid}}) UNWIND $rows AS row "
                    f"MERGE (n:{label}{':Retrievable' if label == 'Chunk' else ''} "
                    "{id:row.id}) SET n += row, "
                    "n.workspace_id=$wid,n.episode_id=$eid,n.snapshot_id=$sid "
                    "MERGE (snap)-[:CONTAINS {workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid}]->(n)",
                    sid=sid,
                    wid=wid,
                    eid=eid,
                    rows=rows,
                ).consume()
        for kind, source_label, source_field, target_label, target_field, items in (
            (
                "HAS_CHUNK",
                "Document",
                "document_id",
                "Chunk",
                "chunk_id",
                document_material.document_chunk_links,
            ),
            (
                "MENTIONS",
                "Chunk",
                "chunk_id",
                "Service",
                "service_key",
                document_material.chunk_service_links,
            ),
            (
                "SUPPORTED_BY",
                "Assertion",
                "assertion_id",
                "Chunk",
                "chunk_id",
                document_material.assertion_chunk_links,
            ),
            (
                "ABOUT_SERVICE",
                "Assertion",
                "assertion_id",
                "Service",
                "service_key",
                document_material.assertion_service_links,
            ),
        ):
            links = tuple(
                {
                    "source_id": _node_id(snapshot_id, source_label, item[source_field]),
                    "target_id": _node_id(snapshot_id, target_label, item[target_field]),
                }
                for item in items
            )
            for rows in _batch(links):
                session.run(
                    f"UNWIND $rows AS row MATCH (a:{source_label} {{id:row.source_id}}) "
                    f"MATCH (b:{target_label} {{id:row.target_id}}) "
                    f"MERGE (a)-[:{kind} {{workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid}]->(b)",
                    rows=rows,
                    wid=wid,
                    eid=eid,
                    sid=sid,
                ).consume()
        operational_projection.project(
            driver,
            material.workspace_id,
            material.episode_id,
            snapshot_id,
            material.operational_material,
        )
        project_handoffs(
            driver, material.workspace_id, material.episode_id, snapshot_id, material.handoffs
        )
        if fail_after == "edges":
            raise GraphBuildError("injected_graph_build_failure")


def validate_projection(
    driver: Driver, snapshot_id: UUID, material: GraphMaterial, *, complete: bool = False
) -> dict[str, int]:
    audit_graph_schema(driver)
    sid, wid, eid = str(snapshot_id), str(material.workspace_id), str(material.episode_id)
    with driver.session(database="neo4j") as session:
        marker = session.run(
            "MATCH (snap:Snapshot {id:$sid,workspace_id:$wid,episode_id:$eid}) "
            "RETURN snap.content_hash AS hash,snap.complete AS complete",
            sid=sid,
            wid=wid,
            eid=eid,
        ).single()
        if (
            marker is None
            or marker["hash"] != material.content_hash
            or marker["complete"] is not complete
        ):
            raise GraphBuildError("graph snapshot marker is invalid")
        counts = {}
        for label, expected in (
            ("Service", len(material.services)),
            ("Observation", len(material.observations)),
            ("Incident", len(material.incidents)),
            ("Document", len(material.document_material.documents)),
            ("Chunk", len(material.document_material.chunks)),
            ("Assertion", len(material.document_material.assertions)),
        ):
            counts[label.lower()] = session.run(
                f"MATCH (n:{label} {{workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}}) "
                "RETURN count(n) AS total",
                wid=wid,
                eid=eid,
                sid=sid,
            ).single(strict=True)["total"]
            if counts[label.lower()] != expected:
                raise GraphBuildError("graph node count differs from authoritative input")
        for labels, expected in (
            ("Observation:Retrievable", len(material.observations)),
            ("Chunk:Retrievable", len(material.document_material.chunks)),
            ("Retrievable", len(material.observations) + len(material.document_material.chunks)),
        ):
            count = session.run(
                f"MATCH (n:{labels} {{workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}}) "
                "RETURN count(n) AS total",
                wid=wid,
                eid=eid,
                sid=sid,
            ).single(strict=True)["total"]
            if count != expected:
                raise GraphBuildError("retrievable node count differs from authoritative input")
        expected_links: Counter[tuple[str, str, str, tuple[str, ...]]] = Counter()
        for label, source_field, rows in (
            ("Service", "service_key", material.services),
            ("Observation", "source_id", material.observations),
            ("Incident", "source_id", material.incidents),
            ("Document", "document_id", material.document_material.documents),
            ("Chunk", "chunk_id", material.document_material.chunks),
            ("Assertion", "assertion_id", material.document_material.assertions),
        ):
            for row in rows:
                source_key = row[source_field]
                expected_links[("CONTAINS", sid, _node_id(snapshot_id, label, source_key), ())] += 1
        for row in material.observation_links:
            expected_links[
                (
                    row["kind"],
                    _node_id(snapshot_id, "Observation", row["observation_id"]),
                    _node_id(snapshot_id, "Service", row["service_key"]),
                    (),
                )
            ] += 1
        for row in material.incident_links:
            expected_links[
                (
                    "HAS_SYMPTOM_ON",
                    _node_id(snapshot_id, "Incident", row["incident_id"]),
                    _node_id(snapshot_id, "Service", row["service_key"]),
                    (),
                )
            ] += 1
        for row in material.calls:
            expected_links[
                (
                    "CALLS",
                    _node_id(snapshot_id, "Service", row["caller_service_key"]),
                    _node_id(snapshot_id, "Service", row["callee_service_key"]),
                    tuple(row["support_observation_ids"]),
                )
            ] += 1
        for kind, source_label, source_field, target_label, target_field, items in (
            (
                "HAS_CHUNK",
                "Document",
                "document_id",
                "Chunk",
                "chunk_id",
                material.document_material.document_chunk_links,
            ),
            (
                "MENTIONS",
                "Chunk",
                "chunk_id",
                "Service",
                "service_key",
                material.document_material.chunk_service_links,
            ),
            (
                "SUPPORTED_BY",
                "Assertion",
                "assertion_id",
                "Chunk",
                "chunk_id",
                material.document_material.assertion_chunk_links,
            ),
            (
                "ABOUT_SERVICE",
                "Assertion",
                "assertion_id",
                "Service",
                "service_key",
                material.document_material.assertion_service_links,
            ),
        ):
            for row in items:
                expected_links[
                    (
                        kind,
                        _node_id(snapshot_id, source_label, row[source_field]),
                        _node_id(snapshot_id, target_label, row[target_field]),
                        (),
                    )
                ] = 1
        relationships = session.run(
            "MATCH (a)-[r]->(b) WHERE r.snapshot_id=$sid "
            "AND coalesce(r.operational,false)=false AND coalesce(r.handoff,false)=false "
            "RETURN type(r) AS kind,a.id AS source,b.id AS target,"
            "r.workspace_id AS wid,r.episode_id AS eid,"
            "a.workspace_id AS awid,a.episode_id AS aeid,a.snapshot_id AS asid,"
            "b.workspace_id AS bwid,b.episode_id AS beid,b.snapshot_id AS bsid,"
            "r.support_observation_ids AS supports",
            sid=sid,
        ).data()
        actual_links: Counter[tuple[str, str, str, tuple[str, ...]]] = Counter()
        for row in relationships:
            if (
                row["wid"] != wid
                or row["eid"] != eid
                or row["awid"] != wid
                or row["aeid"] != eid
                or row["asid"] != sid
                or row["bwid"] != wid
                or row["beid"] != eid
                or row["bsid"] != sid
            ):
                raise GraphBuildError("graph relationship scope is invalid")
            support = row["supports"]
            if support is not None and not isinstance(support, list):
                raise GraphBuildError("graph relationship support is invalid")
            actual_links[(row["kind"], row["source"], row["target"], tuple(support or ()))] += 1
        if actual_links != expected_links:
            raise GraphBuildError("graph relationships differ from authoritative input")
        operational_projection.verify(
            driver,
            material.workspace_id,
            material.episode_id,
            snapshot_id,
            material.operational_material,
        )
        verify_handoffs(
            driver,
            material.workspace_id,
            material.episode_id,
            snapshot_id,
            material.handoffs,
            2**63 - 1,
            full_projection=True,
        )
        counts["operational_entities"] = sum(
            node.kind != "service" for node in material.operational_material.nodes
        )
        counts["calls"] = len(material.calls)
        return counts


def build_snapshot(
    session_factory: sessionmaker[Session],
    driver: Driver,
    *,
    workspace_id: UUID,
    episode_id: UUID,
    artifacts: ArtifactRepository | None = None,
    document_cutoff_ns: int | None = None,
    fail_after: Literal["marker", "services", "observations", "edges", "validated"] | None = None,
    publication_guard: Callable[[Session, UUID], None] | None = None,
    snapshot_id: UUID | None = None,
) -> SnapshotBuild:
    """Publish a new ID only after graph validation; SQL owns the active pointer."""
    migrate_graph(driver)
    snapshot_id = snapshot_id or uuid4()
    with session_factory() as session:
        previous = session.get(SnapshotRow, snapshot_id)
        material = load_graph_material(
            session,
            workspace_id,
            episode_id,
            artifacts=artifacts,
            document_cutoff_ns=document_cutoff_ns,
            membership=previous.membership if previous is not None else None,
        )
    already_ready = False
    with session_factory.begin() as session:
        episode = session.scalar(
            select(EpisodeRow).where(EpisodeRow.id == episode_id).with_for_update()
        )
        if episode is None or episode.workspace_id != workspace_id:
            raise StorageConflict("episode is missing or outside scope")
        if material.history_snapshot_ids:
            pinned = session.scalars(
                select(SnapshotRow)
                .where(
                    SnapshotRow.id.in_(material.history_snapshot_ids),
                    SnapshotRow.workspace_id == workspace_id,
                    SnapshotRow.status == "READY",
                )
                .with_for_update()
            ).all()
            if len(pinned) != len(material.history_snapshot_ids):
                raise StorageConflict("historical snapshots changed before publication")
        existing = session.get(SnapshotRow, snapshot_id)
        if existing is not None:
            if (
                existing.workspace_id != workspace_id
                or existing.episode_id != episode_id
                or existing.content_hash != material.content_hash
                or existing.status not in {"BUILDING", "FAILED", "READY"}
            ):
                raise StorageConflict("snapshot retry identity changed")
            already_ready = existing.status == "READY"
            if not already_ready:
                existing.status, existing.error_code = "BUILDING", None
        else:
            session.add(
                SnapshotRow(
                    id=snapshot_id,
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    content_hash=material.content_hash,
                    membership=snapshot_membership(material),
                    schema_version=GRAPH_SCHEMA_VERSION,
                    status="BUILDING",
                    published_at=None,
                    error_code=None,
                )
            )
    try:
        projection_stage = None if fail_after == "validated" else fail_after
        if not already_ready:
            project_material(driver, snapshot_id, material, fail_after=projection_stage)
        validate_projection(driver, snapshot_id, material, complete=already_ready)
        if fail_after == "validated":
            raise GraphBuildError("injected_graph_build_failure")
        if not already_ready:
            with driver.session(database="neo4j") as session:
                session.run(
                    "MATCH (s:Snapshot {id:$sid,workspace_id:$wid,episode_id:$eid}) "
                    "SET s.complete=true",
                    sid=str(snapshot_id),
                    wid=str(workspace_id),
                    eid=str(episode_id),
                ).consume()
        with session_factory.begin() as session:
            if publication_guard is not None:
                publication_guard(session, snapshot_id)
            row = session.scalar(
                select(SnapshotRow).where(SnapshotRow.id == snapshot_id).with_for_update()
            )
            episode = session.scalar(
                select(EpisodeRow).where(EpisodeRow.id == episode_id).with_for_update()
            )
            if (
                row is None
                or row.status not in ({"READY"} if already_ready else {"BUILDING"})
                or episode is None
            ):
                raise GraphBuildError("snapshot publication state changed")
            if not already_ready:
                row.status = "READY"
                row.published_at = datetime.now(UTC)
                episode.active_snapshot_id = snapshot_id
    except Exception:
        with session_factory.begin() as session:
            row = session.get(SnapshotRow, snapshot_id)
            if row is not None and row.status == "BUILDING":
                row.status = "FAILED"
                row.error_code = "projection_or_validation_failed"
        raise
    return SnapshotBuild(
        snapshot_id,
        episode_id,
        material.content_hash,
        len(material.services),
        len(material.observations),
        len(material.incidents),
        len(material.calls),
        "READY",
        len(material.document_material.documents),
        len(material.document_material.chunks),
        len(material.document_material.assertions),
    )
