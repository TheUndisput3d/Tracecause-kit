"""Typed operational KG extension with source-verified immutable snapshot membership."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, get_args
from uuid import UUID

from neo4j import Driver, Query

from tracecause.graph.errors import GraphReadError
from tracecause.operational.models import OperationalBundle, OperationalEdge, OperationalNode

LABELS = {
    "application": "Application",
    "deployment": "Deployment",
    "version": "SoftwareVersion",
    "configuration": "ConfigurationChange",
    "runtime": "RuntimeInstance",
    "host": "Host",
    "team": "Team",
    "incident": "IncidentRecord",
}


def node_id(snapshot: UUID, logical: str) -> str:
    if logical.startswith("service:"):
        material = f"{snapshot}:Service:{logical.removeprefix('service:')}"
    else:
        material = f"{snapshot}:OperationalEntity:{logical}"
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class OperationalMaterial:
    nodes: tuple[OperationalNode, ...] = ()
    edges: tuple[OperationalEdge, ...] = ()


def assemble(bundle: OperationalBundle | None, workspace: UUID) -> OperationalMaterial:
    if bundle is None:
        return OperationalMaterial()
    nodes: dict[str, OperationalNode] = {}
    supports: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    def entity(identifier: str, kind: Any, label: str, evidence: str) -> str:
        if identifier not in nodes:
            nodes[identifier] = OperationalNode(
                id=identifier, kind=kind, label=label[:200], evidence_ids=[]
            )
        if (
            evidence not in nodes[identifier].evidence_ids
            and len(nodes[identifier].evidence_ids) < 100
        ):
            nodes[identifier].evidence_ids.append(evidence)
        return identifier

    def edge(source: str, target: str, kind: str, evidence: str) -> None:
        supports[(source, target, kind)].add(evidence)

    for event in sorted(bundle.events, key=lambda e: (e.occurred_at, str(e.id))):
        evidence = f"operation:{event.id}"
        key = event.service.key(workspace)
        service = entity("service:" + key, "service", event.service.name, evidence)
        app = entity(
            "application:" + bundle.application_id, "application", bundle.application_name, evidence
        )
        edge(app, service, "HAS_SERVICE", evidence)
        details = event.details
        if event.kind in {"deployment", "runtime", "configuration"}:
            runtime = entity(
                "runtime:" + str(details["runtime_id"]),
                "runtime",
                str(details["runtime_id"]),
                evidence,
            )
            edge(runtime, service, "INSTANCE_OF", evidence)
        if event.kind in {"deployment", "runtime"}:
            version = entity(
                f"version:{key}:{details['version']}",
                "version",
                f"{event.service.name} {details['version']}",
                evidence,
            )
            edge(version, service, "VERSION_OF", evidence)
            edge(version, runtime, "RUNS_ON", evidence)
        if event.kind == "deployment":
            deployment = entity(
                "deployment:" + str(event.id),
                "deployment",
                "Deploy " + str(details["version"]),
                evidence,
            )
            edge(deployment, version, "DEPLOYS", evidence)
        elif event.kind == "runtime":
            host = entity("host:" + str(details["host"]), "host", str(details["host"]), evidence)
            edge(runtime, host, "HOSTED_ON", evidence)
        elif event.kind == "configuration":
            config = entity(
                "configuration:" + str(event.id),
                "configuration",
                f"{details['key']} = {details['value']}",
                evidence,
            )
            edge(config, runtime, "CONFIGURES", evidence)
            edge(config, service, "CONFIGURES", evidence)
        elif event.kind == "ownership":
            team = entity("team:" + str(details["team"]), "team", str(details["team"]), evidence)
            edge(service, team, "OWNED_BY", evidence)
        elif event.kind == "incident_note":
            incident = entity(
                "incident:" + str(event.id), "incident", str(details["title"]), evidence
            )
            edge(incident, service, "RECORDED_ON", evidence)
    return OperationalMaterial(
        tuple(nodes[k] for k in sorted(nodes)),
        tuple(
            OperationalEdge.model_validate(
                dict(source=a, target=b, relationship=kind, evidence_ids=sorted(ids)[:100])
            )
            for (a, b, kind), ids in sorted(supports.items())
        ),
    )


def project(
    driver: Driver, workspace: UUID, episode: UUID, snapshot: UUID, material: OperationalMaterial
) -> None:
    scope: dict[str, Any] = dict(wid=str(workspace), eid=str(episode), sid=str(snapshot))
    with driver.session(database="neo4j") as session:
        for kind, label in LABELS.items():
            rows = [
                dict(id=node_id(snapshot, n.id), key=n.id, payload=n.model_dump_json())
                for n in material.nodes
                if n.kind == kind
            ]
            if rows:
                session.run(
                    Query(
                        f"UNWIND $rows AS row MERGE (n:OperationalEntity:{label} {{id:row.id}}) "
                        "SET n.logical_id=row.key,n.payload=row.payload,n.workspace_id=$wid,"
                        "n.episode_id=$eid,n.snapshot_id=$sid",
                        timeout=5,
                    ),
                    rows=rows,
                    **scope,
                ).consume()
        kinds = get_args(OperationalEdge.model_fields["relationship"].annotation)
        for kind in kinds:
            rows = [
                dict(
                    source=node_id(snapshot, e.source),
                    target=node_id(snapshot, e.target),
                    payload=e.model_dump_json(),
                )
                for e in material.edges
                if e.relationship == kind
            ]
            if rows:
                session.run(
                    Query(
                        "UNWIND $rows AS row MATCH (a {id:row.source,workspace_id:$wid,"
                        "episode_id:$eid,snapshot_id:$sid}) "
                        "MATCH (b {id:row.target,workspace_id:$wid,"
                        "episode_id:$eid,snapshot_id:$sid}) "
                        f"MERGE (a)-[r:{kind} {{operational:true,workspace_id:$wid,"
                        "episode_id:$eid,snapshot_id:$sid}]->(b) "
                        "SET r.payload=row.payload",
                        timeout=5,
                    ),
                    rows=rows,
                    **scope,
                ).consume()


def _payload(value: object) -> object:
    if not isinstance(value, str):
        raise GraphReadError("operational projection payload is invalid")
    try:
        return json.loads(value)
    except (ValueError, TypeError) as exc:
        raise GraphReadError("operational projection payload is invalid") from exc


def verify(
    driver: Driver, workspace: UUID, episode: UUID, snapshot: UUID, material: OperationalMaterial
) -> None:
    scope: dict[str, Any] = dict(wid=str(workspace), eid=str(episode), sid=str(snapshot))
    with driver.session(database="neo4j") as session:
        nodes = session.run(
            Query(
                "MATCH (n:OperationalEntity {snapshot_id:$sid}) "
                "RETURN n.id AS id,n.payload AS payload,labels(n) AS labels,"
                "n.workspace_id AS wid,n.episode_id AS eid LIMIT 2501",
                timeout=5,
            ),
            **scope,
        ).data()
        edges = session.run(
            Query(
                "MATCH (a)-[r {operational:true}]->(b) "
                "WHERE r.snapshot_id=$sid OR a.snapshot_id=$sid OR b.snapshot_id=$sid "
                "RETURN a.id AS source,b.id AS target,type(r) AS kind,r.payload AS payload,"
                "r.workspace_id AS rwid,r.episode_id AS reid,r.snapshot_id AS rsid,"
                "a.workspace_id AS awid,b.workspace_id AS bwid,"
                "a.episode_id AS aeid,b.episode_id AS beid,"
                "a.snapshot_id AS asid,b.snapshot_id AS bsid LIMIT 5001",
                timeout=5,
            ),
            **scope,
        ).data()
    expected_nodes = {node_id(snapshot, n.id): n for n in material.nodes if n.kind != "service"}
    if len(nodes) != len(expected_nodes) or {n["id"] for n in nodes} != set(expected_nodes):
        raise GraphReadError("operational graph entity membership differs from source")
    for row in nodes:
        expected = expected_nodes[row["id"]]
        if (
            row["wid"] != str(workspace)
            or row["eid"] != str(episode)
            or _payload(row["payload"]) != expected.model_dump(mode="json")
            or LABELS[expected.kind] not in row["labels"]
        ):
            raise GraphReadError("operational graph entity differs from source")
    expected_edges = {
        (node_id(snapshot, e.source), node_id(snapshot, e.target), e.relationship): e.model_dump(
            mode="json"
        )
        for e in material.edges
    }
    if len(edges) != len(expected_edges):
        raise GraphReadError("operational graph relationship membership differs from source")
    for row in edges:
        if any(
            row[k] != str(v)
            for k, v in (
                ("rwid", workspace),
                ("reid", episode),
                ("rsid", snapshot),
                ("awid", workspace),
                ("bwid", workspace),
                ("aeid", episode),
                ("beid", episode),
                ("asid", snapshot),
                ("bsid", snapshot),
            )
        ):
            raise GraphReadError("operational graph relationship crosses source scope")
        expected_edge = expected_edges.get((row["source"], row["target"], row["kind"]))
        if expected_edge is None or _payload(row["payload"]) != expected_edge:
            raise GraphReadError("operational graph relationship differs from source")
