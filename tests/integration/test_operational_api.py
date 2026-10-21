"""Operational provenance and history gates on actual PostgreSQL and Neo4j."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration.test_api import HEADERS, api  # noqa: F401
from tests.integration.test_persistence_jobs import (  # noqa: F401
    ROOT,
    connection_url,
    factory,
    test_database_name,
)

from tracecause.graph.errors import GraphReadError
from tracecause.graph.maintenance import retire_snapshot
from tracecause.graph.projection import build_snapshot
from tracecause.jobs.api_worker import ApiWorker
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID


def import_operational(client, resources, name):
    directory = ROOT / "data/operational-lab/episodes" / name
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["episode_id"] = str(uuid4())
    files = [
        (
            "files",
            (s["logical_name"], (directory / s["logical_name"]).read_bytes(), "application/json"),
        )
        for s in manifest["sources"]
    ]
    response = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    assert ApiWorker(resources).run_once() == job_id
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job
    return manifest, dict(
        episode_id=manifest["episode_id"],
        snapshot_id=job["result_id"],
        as_of=manifest["event_interval"]["end"],
        temporal_mode="event_time_replay",
    )


def test_operational_receipts_projection_cutoff_and_tampering(api):  # noqa: F811
    client, resources = api
    manifest, scope = import_operational(client, resources, "5ff7a513-f40a-4275-b3d0-dae846cbbd99")
    response = client.get("/api/v1/operations", params=scope)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["facts"] and result["nodes"] and result["edges"]
    captured = json.loads((ROOT / "data/operational-lab/index.json").read_bytes())["episodes"]
    cursor = next(
        row["analysis_end"]
        for row in captured
        if row["episode_id"] == "5ff7a513-f40a-4275-b3d0-dae846cbbd99"
    )
    fractional_scope = {**scope, "as_of": cursor}
    replayed = client.get("/api/v1/operations", params=fractional_scope)
    assert replayed.status_code == 200, replayed.text
    signals = [
        change["signal"]
        for change in replayed.json()["change_reasoning"]["changes"]
        if change["signal"]
    ]
    assert signals and any(signal["degradation_detected"] for signal in signals)
    opened_signal = client.get(
        "/api/v1/evidence/" + signals[0]["observation_evidence_id"], params=fractional_scope
    )
    assert opened_signal.status_code == 200, opened_signal.text
    timeline = client.get(
        f"/api/v1/episodes/{manifest['episode_id']}/series", params=fractional_scope
    )
    assert timeline.status_code == 200, timeline.text
    points = timeline.json()["points"]
    buckets = {(point["service_id"], point["start_ns"], point["end_ns"]) for point in points}
    assert len(buckets) == len(points) and len(points) >= 20
    assert {n["kind"] for n in result["nodes"]} >= {
        "application",
        "service",
        "runtime",
        "host",
        "team",
        "configuration",
    }
    for fact in result["facts"]:
        opened = client.get("/api/v1/operations/evidence/" + fact["evidence_id"], params=scope)
        assert opened.status_code == 200, opened.text
        assert opened.json()["event"] == fact["event"]
        assert json.loads(opened.json()["source_json"]) == fact["event"]
        cutoff = datetime.fromisoformat(fact["event"]["occurred_at"]) - timedelta(microseconds=1)
        if cutoff >= datetime.fromisoformat(manifest["event_interval"]["start"]):
            hidden = client.get(
                "/api/v1/operations/evidence/" + fact["evidence_id"],
                params={**scope, "as_of": cutoff.isoformat()},
            )
            assert hidden.status_code == 404, hidden.text
    assert (
        client.get("/api/v1/operations", params={**scope, "episode_id": str(uuid4())}).status_code
        == 404
    )
    with resources.driver.session(database="neo4j") as graph:
        row = graph.run(
            "MATCH (n:OperationalEntity {snapshot_id:$sid}) "
            "RETURN n.id AS id,n.payload AS payload LIMIT 1",
            sid=scope["snapshot_id"],
        ).single(strict=True)
        graph.run(
            "MATCH (n:OperationalEntity {id:$id}) SET n.payload=$payload",
            id=row["id"],
            payload='{"tampered":true}',
        ).consume()
        try:
            assert client.get("/api/v1/operations", params=scope).status_code == 409
        finally:
            graph.run(
                "MATCH (n:OperationalEntity {id:$id}) SET n.payload=$payload",
                id=row["id"],
                payload=row["payload"],
            ).consume()
    assert client.get("/api/v1/operations", params=scope).json() == result


def test_historical_snapshots_are_pinned_and_retained(api):  # noqa: F811
    client, resources = api
    directories = sorted(
        (ROOT / "data/operational-lab/episodes").iterdir(),
        key=lambda p: json.loads((p / "manifest.json").read_bytes())["event_interval"]["start"],
    )
    shop = [
        p
        for p in directories
        if json.loads((p / "operational.json").read_bytes())["application_id"] == "shop-chain"
    ]
    assert len(shop) == 2
    _, prior = import_operational(client, resources, shop[0].name)
    _, current = import_operational(client, resources, shop[1].name)
    before = client.get("/api/v1/operations", params=current)
    assert before.status_code == 200, before.text
    matches = before.json()["history"]["matches"]
    assert any(m["snapshot_id"] == prior["snapshot_id"] for m in matches)
    build_snapshot(
        resources.factory,
        resources.driver,
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=UUID(prior["episode_id"]),
        artifacts=resources.artifacts,
    )
    assert client.get("/api/v1/operations", params=current).json() == before.json()
    plan = retire_snapshot(
        resources.factory,
        resources.driver,
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=UUID(prior["episode_id"]),
        snapshot_id=UUID(prior["snapshot_id"]),
    )
    assert not plan.active and not plan.eligible and plan.historical_snapshots >= 1
    with pytest.raises(GraphReadError, match="historical"):
        retire_snapshot(
            resources.factory,
            resources.driver,
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=UUID(prior["episode_id"]),
            snapshot_id=UUID(prior["snapshot_id"]),
            apply=True,
        )
