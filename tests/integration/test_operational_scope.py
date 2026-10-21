"""Environment scoping with actual PostgreSQL, Neo4j and HTTP import pipelines."""

import hashlib
import json
from uuid import UUID, uuid4

from tests.integration.test_api import HEADERS, api  # noqa: F401
from tests.integration.test_operational_api import import_operational
from tests.integration.test_persistence_jobs import (  # noqa: F401
    ROOT,
    connection_url,
    factory,
    test_database_name,
)

from tracecause.jobs.api_worker import ApiWorker
from tracecause.storage.schema import EpisodeRow, SnapshotRow


def import_other_environment(client, resources):
    directory = ROOT / "data/operational-lab/episodes/5ff7a513-f40a-4275-b3d0-dae846cbbd99"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["episode_id"] = str(uuid4())
    manifest["origin"] = "synthetic"
    manifest["public_notes"] = (
        "Synthetic environment-scope adversary derived from captured source. Integration test only."
    )
    files = []
    for source in manifest["sources"]:
        if source["kind"] == "operational_json":
            bundle = json.loads((directory / source["logical_name"]).read_bytes())
            for event in bundle["events"]:
                event["service"]["environment"] = "production-test"
            content = json.dumps(bundle).encode()
        else:
            rows = []
            for line in (directory / source["logical_name"]).read_bytes().splitlines():
                row = json.loads(line)
                for resource in row["resourceSpans"]:
                    for attribute in resource["resource"]["attributes"]:
                        if attribute["key"] == "deployment.environment.name":
                            attribute["value"]["stringValue"] = "production-test"
                rows.append(json.dumps(row).encode() + b"\n")
            content = b"".join(rows)
        source["sha256"], source["bytes"] = hashlib.sha256(content).hexdigest(), len(content)
        files.append(("files", (source["logical_name"], content, "application/json")))
    response = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    assert ApiWorker(resources).run_once(job_id=job_id) == job_id
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job
    return manifest["episode_id"], job["result_id"]


def test_history_membership_never_pins_or_replays_another_environment(api):  # noqa: F811
    client, resources = api
    foreign_episode, foreign_snapshot = import_other_environment(client, resources)
    _, same = import_operational(client, resources, "5ff7a513-f40a-4275-b3d0-dae846cbbd99")
    _, current = import_operational(client, resources, "95812eb4-28b8-481a-a3de-a6f92a194b8d")
    with resources.factory() as session:
        foreign = session.get(EpisodeRow, UUID(foreign_episode))
        current_snapshot = session.get(SnapshotRow, UUID(current["snapshot_id"]))
        assert foreign.data_quality["operational_environment"] == "production-test"
        membership = dict(current_snapshot.membership)
        assert same["snapshot_id"] in membership["history_snapshot_ids"]
        assert foreign_snapshot not in membership["history_snapshot_ids"]
    response = client.get("/api/v1/operations", params=current)
    assert response.status_code == 200, response.text
    assert [item["snapshot_id"] for item in response.json()["history"]["matches"]] == [
        same["snapshot_id"]
    ]
    try:
        with resources.factory.begin() as session:
            snapshot = session.get(SnapshotRow, UUID(current["snapshot_id"]))
            snapshot.membership = {
                **membership,
                "history_snapshot_ids": [foreign_snapshot, same["snapshot_id"]],
            }
        rejected = client.get("/api/v1/operations", params=current)
        assert rejected.status_code == 409, rejected.text
    finally:
        with resources.factory.begin() as session:
            snapshot = session.get(SnapshotRow, UUID(current["snapshot_id"]))
            snapshot.membership = membership
    assert client.get("/api/v1/operations", params=current).json() == response.json()
