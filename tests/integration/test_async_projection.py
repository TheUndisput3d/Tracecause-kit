"""Actual Neo4j projection, retry and tamper verification of async proof edges."""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from tests.unit.test_operational_handoffs import BASE_NS, derive, source_pair

from tracecause.graph.errors import GraphReadError
from tracecause.graph.schema import driver_from_auth_file
from tracecause.ingestion.normalize import normalize_otlp, serialized_span, serialized_spans
from tracecause.operational.handoffs import derive_handoffs, project_handoffs, verify_handoffs

ROOT = Path(__file__).resolve().parents[2]


def test_real_neo4j_async_projection_is_repeatable_scoped_and_tamper_checked():
    producer, consumer = source_pair()
    records = derive(producer, consumer)
    workspace, episode, snapshot = producer.workspace_id, producer.episode_id, uuid4()
    nodes = [
        dict(id=hashlib.sha256(f"{snapshot}:Service:{key}".encode()).hexdigest(), service_key=key)
        for key in (producer.service_key, consumer.service_key)
    ]
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        try:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "UNWIND $nodes AS row CREATE (s:Service {id:row.id,service_key:row.service_key,"
                    "workspace_id:$wid,episode_id:$eid,snapshot_id:$sid})",
                    nodes=nodes,
                    wid=str(workspace),
                    eid=str(episode),
                    sid=str(snapshot),
                ).consume()
            project_handoffs(driver, workspace, episode, snapshot, records)
            project_handoffs(driver, workspace, episode, snapshot, records)
            verify_handoffs(driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000)
            verify_handoffs(driver, workspace, episode, snapshot, (), BASE_NS + 1_000_000_000)
            with pytest.raises(GraphReadError, match="complete.*membership"):
                verify_handoffs(
                    driver,
                    workspace,
                    episode,
                    snapshot,
                    (),
                    BASE_NS + 1_000_000_000,
                    full_projection=True,
                )
            with pytest.raises(GraphReadError, match="scope"):
                verify_handoffs(
                    driver, uuid4(), episode, snapshot, records, BASE_NS + 10_000_000_000
                )
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (h:HandoffObservation {snapshot_id:$sid}) "
                    "SET h.producer_span_id='ffffffffffffffff'",
                    sid=str(snapshot),
                ).consume()
            with pytest.raises(GraphReadError, match="differs"):
                verify_handoffs(
                    driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000
                )
            project_handoffs(driver, workspace, episode, snapshot, records)
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (s:Service {id:$id}) SET s.service_key='forged'", id=nodes[0]["id"]
                ).consume()
            with pytest.raises(GraphReadError, match="endpoint service"):
                verify_handoffs(
                    driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000
                )
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (s:Service {id:$id}) SET s.service_key=$key",
                    id=nodes[0]["id"],
                    key=producer.service_key,
                ).consume()
                graph.run(
                    "MATCH (s:Service {id:$id}) REMOVE s:Service", id=nodes[0]["id"]
                ).consume()
            with pytest.raises(GraphReadError, match="endpoint service"):
                verify_handoffs(
                    driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000
                )
            with driver.session(database="neo4j") as graph:
                graph.run("MATCH (s {id:$id}) SET s:Service", id=nodes[0]["id"]).consume()
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH ()-[r:MESSAGE_FLOW {snapshot_id:$sid}]->() DELETE r", sid=str(snapshot)
                ).consume()
            with pytest.raises(GraphReadError, match="differs"):
                verify_handoffs(
                    driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000
                )
            project_handoffs(driver, workspace, episode, snapshot, records)
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (p:Service {id:$pid}),(c:Service {id:$cid}) "
                    "CREATE (p)-[:MESSAGE_FLOW {handoff:true,workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid,evidence_id:'orphan'}]->(c)",
                    pid=nodes[0]["id"],
                    cid=nodes[1]["id"],
                    wid=str(workspace),
                    eid=str(episode),
                    sid=str(snapshot),
                ).consume()
            with pytest.raises(GraphReadError, match="membership"):
                verify_handoffs(
                    driver, workspace, episode, snapshot, records, BASE_NS + 10_000_000_000
                )
        finally:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (n {snapshot_id:$sid}) DETACH DELETE n", sid=str(snapshot)
                ).consume()


def test_full_captured_media_handoffs_preserve_proofs_without_parallel_edge_join() -> None:
    """Exercise the actual large public capture under the unchanged 5s query bound."""
    index = json.loads((ROOT / "data/operational-lab/index.json").read_bytes())
    entry = next(
        row
        for row in index["episodes"]
        if row["application_id"] == "media-pipeline" and row["ordinal"] == 1
    )
    directory = ROOT / "data/operational-lab/episodes" / entry["episode_id"]
    manifest = json.loads((directory / "manifest.json").read_bytes())
    workspace, episode = UUID(manifest["workspace_id"]), UUID(entry["episode_id"])
    with (directory / "traces.jsonl").open("rb") as stream:
        batch = normalize_otlp(
            stream,
            encoding="jsonl",
            workspace_id=workspace,
            episode_id=episode,
            expected_source_hash=entry["source_sha256"],
            source_name="traces.jsonl",
        )
    assert not batch.fatal and batch.rejected_count == 0
    assert batch.accepted_count == entry["accepted_spans"]
    normalized_hash = hashlib.sha256(serialized_spans(batch.spans)).hexdigest()
    locators = {}
    offset = 0
    for span in batch.spans:
        locators[(span.trace_id, span.span_id)] = (normalized_hash, offset)
        offset += len(serialized_span(span))
    cutoff = max(int(span.end_ns) for span in batch.spans)
    records = derive_handoffs(
        tuple(span for span in batch.spans if span.kind in {"PRODUCER", "CONSUMER"}),
        locators,
        workspace,
        episode,
        cutoff,
    )
    assert len(records) >= 400  # The retained capture contains hundreds of parallel proofs.
    snapshot = uuid4()
    keys = {r.producer.service_key for r in records} | {r.consumer.service_key for r in records}
    nodes = [
        dict(id=hashlib.sha256(f"{snapshot}:Service:{key}".encode()).hexdigest(), service_key=key)
        for key in sorted(keys)
    ]
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        try:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "UNWIND $nodes AS row CREATE (s:Service {id:row.id,service_key:row.service_key,"
                    "workspace_id:$wid,episode_id:$eid,snapshot_id:$sid})",
                    nodes=nodes,
                    wid=str(workspace),
                    eid=str(episode),
                    sid=str(snapshot),
                ).consume()
            project_handoffs(driver, workspace, episode, snapshot, records)
            verify_handoffs(
                driver, workspace, episode, snapshot, records, cutoff, full_projection=True
            )
            earlier = sorted(int(r.consumer.end_ns) for r in records)[len(records) // 2]
            eligible = tuple(
                r for r in records if max(int(r.producer.end_ns), int(r.consumer.end_ns)) <= earlier
            )
            verify_handoffs(driver, workspace, episode, snapshot, eligible, earlier)
        finally:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (n {snapshot_id:$sid}) DETACH DELETE n", sid=str(snapshot)
                ).consume()
