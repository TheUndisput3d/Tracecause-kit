"""HTTP boundary tests against disposable real PostgreSQL and actual Neo4j."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from tests.integration.test_persistence_jobs import (  # noqa: F401
    ROOT,
    connection_url,
    factory,
    test_database_name,
)

from tracecause.api.main import create_app
from tracecause.api.resources import Resources
from tracecause.config.settings import RuntimeSettings
from tracecause.graph.schema import driver_from_auth_file
from tracecause.jobs.api_worker import ApiWorker
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.schema import EpisodeRow

TOKEN = "api-test-only-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "import-one"}


@pytest.fixture(scope="module")
def api(factory, tmp_path_factory):  # noqa: F811
    root = tmp_path_factory.mktemp("api")
    settings = RuntimeSettings(
        public_read_only=False,
        operator_token=SecretStr(TOKEN),
        artifact_root=root / "artifacts",
        staging_root=root / "staging",
    )
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        resources = Resources(settings, factory, driver, ArtifactRepository(settings.artifact_root))
        with TestClient(create_app(settings, resources), raise_server_exceptions=False) as client:
            yield client, resources
        with factory() as session:
            ids = [str(i) for i in session.scalars(select(EpisodeRow.id))]
        with driver.session(database="neo4j") as graph:
            graph.run("MATCH (n) WHERE n.episode_id IN $ids DETACH DELETE n", ids=ids).consume()


def bundle():
    directory = ROOT / "data/generated/seed-20261010/03c9ef66-2609-5aec-86fa-9dda41e1c9af"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["episode_id"] = str(uuid4())
    files = [
        (
            "files",
            (s["logical_name"], (directory / s["logical_name"]).read_bytes(), "application/json"),
        )
        for s in manifest["sources"]
    ]
    return manifest, files


def ready_episode(client, resources, *, key=None):
    manifest, files = bundle()
    response = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    assert ApiWorker(resources).run_once() == UUID(identifier)
    job = client.get(f"/api/v1/jobs/{identifier}").json()
    assert job["status"] == "SUCCEEDED", job
    episode = client.get(f"/api/v1/episodes/{manifest['episode_id']}").json()
    assert episode["active_snapshot_id"] == job["result_id"]
    return manifest, files, episode, identifier


def request_for(manifest, episode):
    return dict(
        episode_id=manifest["episode_id"],
        snapshot_id=episode["active_snapshot_id"],
        as_of=manifest["event_interval"]["end"],
        temporal_mode="event_time_replay",
        question="Which service should be checked?",
        symptom_service_id=None,
        retrieval_mode="LEXICAL",
        ranking_mode="heuristic",
        generation_mode="template",
    )


def test_api_import_snapshot_supports_earlier_investigation_cutoff(api):
    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    cutoff = datetime.fromisoformat(manifest["baseline_interval"]["end"]) + timedelta(seconds=60)
    request = {**request_for(manifest, episode), "as_of": cutoff.isoformat()}
    posted = client.post(
        "/api/v1/investigations",
        json=request,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert posted.status_code == 202, posted.text
    assert ApiWorker(resources).run_once() == UUID(posted.json()["job_id"])
    saved = client.get(f"/api/v1/investigations/{posted.json()['investigation_id']}").json()
    assert saved["status"] == "SUCCEEDED", saved
    result = saved["result"]
    assert result["narrative"]["status"] == "supported_hypotheses"
    assert "candidate_support_unavailable_in_snapshot" not in result["warnings"]
    assert result["evidence"]
    for item in result["evidence"]:
        if item["interval"] is not None:
            assert datetime.fromisoformat(item["interval"]["end"]) <= cutoff


def test_real_http_import_job_graph_investigation_and_every_citation(api):
    client, resources = api
    assert client.get("/health/ready").status_code == 200
    manifest, files, episode, job_id = ready_episode(client, resources, key="workflow")
    repeated = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": "workflow"},
    )
    assert repeated.status_code == 202 and repeated.json()["job_id"] == job_id
    other, other_files = bundle()
    conflict = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(other)},
        files=other_files,
        headers={**HEADERS, "Idempotency-Key": "workflow"},
    )
    assert conflict.status_code == 409
    params = dict(
        episode_id=manifest["episode_id"],
        snapshot_id=episode["active_snapshot_id"],
        as_of=manifest["event_interval"]["end"],
        temporal_mode="event_time_replay",
    )
    graph = client.get("/api/v1/graph", params=params)
    assert graph.status_code == 200, graph.text
    assert graph.json()["nodes"] and graph.json()["edges"]
    points = client.get(f"/api/v1/episodes/{manifest['episode_id']}/series", params=params)
    assert points.status_code == 200, points.text
    assert points.json()["points"] and isinstance(points.json()["points"][0]["end_ns"], str)
    request = request_for(manifest, episode)
    posted = client.post(
        "/api/v1/investigations",
        json=request,
        headers={**HEADERS, "Idempotency-Key": "investigate-workflow"},
    )
    assert posted.status_code == 202, posted.text
    identifier = posted.json()["investigation_id"]
    assert client.get(f"/api/v1/investigations/{identifier}").json()["status"] == "QUEUED"
    assert client.get(f"/api/v1/investigations/{identifier}/export").status_code == 409
    assert ApiWorker(resources).run_once() == UUID(posted.json()["job_id"])
    response = client.get(f"/api/v1/investigations/{identifier}")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["status"] == "SUCCEEDED", view
    result = view["result"]
    assert result["evidence"]
    for evidence in result["evidence"]:
        opened = client.get("/api/v1/evidence/" + evidence["evidence_id"], params=params)
        assert opened.status_code == 200, opened.text
        assert opened.json()["text"] == evidence["text"]
    exported = client.get(f"/api/v1/investigations/{identifier}/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    assert client.get(f"/api/v1/episodes/{manifest['episode_id']}/investigations").json()["items"]
    for response in (graph, points, exported):
        for secret in (
            TOKEN,
            str(resources.artifacts.root),
            "accepted_target",
            "postgresql://",
            "private_run_reference",
        ):
            assert secret not in response.text
    assert client.get("/api/v1/graph", params={**params, "max_hops": 4}).status_code == 422
    assert (
        client.get("/api/v1/graph", params={**params, "snapshot_id": str(uuid4())}).status_code
        == 404
    )
    assert (
        client.get("/api/v1/graph", params={**params, "as_of": "2000-01-01T00:00:00Z"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/graph", params={**params, "temporal_mode": "available_time_replay"}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/investigations", json=request).status_code == 401
    duplicate = client.post(
        "/api/v1/investigations",
        json=request,
        headers={**HEADERS, "Idempotency-Key": "investigate-workflow"},
    )
    assert duplicate.json()["investigation_id"] == identifier
    assert (
        client.post(
            "/api/v1/investigations",
            json={**request, "question": "different"},
            headers={**HEADERS, "Idempotency-Key": "investigate-workflow"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/investigations",
            json={**request, "generation_mode": "ollama"},
            headers={**HEADERS, "Idempotency-Key": "disabled"},
        ).status_code
        == 403
    )


def test_upload_rejections_and_public_replay(api):
    client, resources = api
    manifest, files = bundle()
    assert (
        client.post(
            "/api/v1/imports", data={"manifest": json.dumps(manifest)}, files=files
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/imports",
            data={"manifest": json.dumps(manifest)},
            files=[("files", ("../traces.json", b"bad", "application/json"))],
            headers=HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/imports",
            data={"manifest": json.dumps(manifest)},
            files=[("files", (files[0][1][0], b"bad", "application/json"))],
            headers=HEADERS,
        ).status_code
        == 422
    )
    changed = {**manifest, "workspace_id": str(uuid4())}
    assert (
        client.post(
            "/api/v1/imports", data={"manifest": json.dumps(changed)}, files=files, headers=HEADERS
        ).status_code
        == 422
    )
    assert client.get("/api/v1/episodes", params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/episodes", params={"cursor": "bad"}).status_code == 422
    settings = RuntimeSettings(
        artifact_root=resources.artifacts.root, staging_root=resources.staging
    )
    with TestClient(create_app(settings, resources)) as public:
        assert public.get("/api/v1/episodes").status_code == 200
        assert (
            public.post("/api/v1/jobs/" + str(uuid4()) + "/cancel", headers=HEADERS).status_code
            == 403
        )
        assert public.post("/api/v1/imports", headers=HEADERS).status_code == 403


def test_queue_cap_cancellation_and_scoped_paging(api):
    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    request = request_for(manifest, episode)
    jobs = []
    for index in range(10):
        r = client.post(
            "/api/v1/investigations",
            json=request,
            headers={**HEADERS, "Idempotency-Key": f"cap-{index}"},
        )
        assert r.status_code == 202, r.text
        jobs.append(r.json()["job_id"])
    assert (
        client.post(
            "/api/v1/investigations",
            json=request,
            headers={**HEADERS, "Idempotency-Key": "cap-over"},
        ).status_code
        == 429
    )
    for job in jobs:
        r = client.post(f"/api/v1/jobs/{job}/cancel", headers=HEADERS)
        assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert ApiWorker(resources).run_once() is None
    first = client.get("/api/v1/episodes", params={"limit": 1}).json()
    assert first["next_cursor"]
    second = client.get(
        "/api/v1/episodes", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert first["items"][0]["episode_id"] != second["items"][0]["episode_id"]
    assert (
        client.get(
            "/api/v1/episodes", params={"cursor": first["next_cursor"], "origin": "captured_demo"}
        ).status_code
        == 422
    )


def test_multiple_telemetry_sources_and_markdown_are_imported(api):
    import hashlib

    from sqlalchemy import func

    from tracecause.storage.schema import DocumentRow, SpanRow

    client, resources = api
    manifest, files = bundle()
    lines = files[0][1][1].splitlines(keepends=True)
    payloads = {
        "part-a.jsonl": b"".join(lines[: len(lines) // 2]),
        "part-b.jsonl": b"".join(lines[len(lines) // 2 :]),
        "demo-doc-01.md": (ROOT / "data/runbooks/demo/demo-doc-01.md").read_bytes(),
    }
    manifest["sources"] = [
        dict(
            logical_name=name,
            kind="markdown" if name.endswith(".md") else "otlp_jsonl",
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            published_at=None,
        )
        for name, raw in payloads.items()
    ]
    response = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=[
            ("files", (name, raw, "application/octet-stream")) for name, raw in payloads.items()
        ],
        headers={**HEADERS, "Idempotency-Key": "multiple-sources"},
    )
    assert response.status_code == 202, response.text
    ApiWorker(resources).run_once()
    job = client.get("/api/v1/jobs/" + response.json()["job_id"]).json()
    assert job["status"] == "SUCCEEDED", job
    with resources.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SpanRow)
                .where(SpanRow.episode_id == UUID(manifest["episode_id"]))
            )
            > 0
        )
        assert (
            session.scalar(select(DocumentRow).where(DocumentRow.source_id == "demo-doc-01"))
            is not None
        )


def test_cancellation_at_publication_does_not_replace_active_snapshot(api):
    from tracecause.graph.projection import build_snapshot
    from tracecause.jobs.api_worker import JobCancelled
    from tracecause.jobs.queue import JobQueue
    from tracecause.storage.schema import JobRow, SnapshotRow

    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    posted = client.post(
        "/api/v1/investigations",
        json=request_for(manifest, episode),
        headers={**HEADERS, "Idempotency-Key": "cancel-at-publication"},
    )
    job_id = UUID(posted.json()["job_id"])
    worker = ApiWorker(resources)
    claimed = worker.queue.claim(worker.worker_id, kinds=("INVESTIGATE",), job_id=job_id)
    assert claimed is not None
    JobQueue(resources.factory).cancel(job_id)
    snapshot_id = uuid4()
    with pytest.raises(JobCancelled):
        build_snapshot(
            resources.factory,
            resources.driver,
            workspace_id=claimed.workspace_id,
            episode_id=claimed.episode_id,
            artifacts=resources.artifacts,
            snapshot_id=snapshot_id,
            publication_guard=lambda session, result: worker.guard(session, job_id, result),
        )
    with resources.factory() as session:
        assert session.get(SnapshotRow, snapshot_id).status == "FAILED"
        assert (
            str(session.get(EpisodeRow, claimed.episode_id).active_snapshot_id)
            == episode["active_snapshot_id"]
        )
    with resources.factory.begin() as session:
        row = session.get(JobRow, job_id)
        row.status = "CANCELLED"
        row.lease_owner = None
        row.lease_until = None


def test_retry_reuses_failed_snapshot_identity_and_ready_replay_is_immutable(api):
    from tracecause.graph.projection import GraphBuildError, build_snapshot
    from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
    from tracecause.storage.schema import SnapshotRow

    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    identifier = uuid4()
    kwargs = dict(
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=UUID(manifest["episode_id"]),
        artifacts=resources.artifacts,
        snapshot_id=identifier,
    )
    with pytest.raises(GraphBuildError):
        build_snapshot(resources.factory, resources.driver, **kwargs, fail_after="services")
    result = build_snapshot(resources.factory, resources.driver, **kwargs)
    assert result.snapshot_id == identifier
    with resources.factory() as session:
        timestamp = session.get(SnapshotRow, identifier).published_at
    assert build_snapshot(resources.factory, resources.driver, **kwargs).snapshot_id == identifier
    with resources.factory() as session:
        assert session.get(SnapshotRow, identifier).published_at == timestamp
    params = dict(
        episode_id=manifest["episode_id"],
        snapshot_id=episode["active_snapshot_id"],
        as_of=manifest["event_interval"]["end"],
    )
    assert client.get("/api/v1/graph", params=params).status_code == 200


def test_published_evaluations_only_serve_sanitized_immutable_aggregates(api):
    from datetime import UTC, datetime

    from tracecause.api.publish_report import publish
    from tracecause.contracts.models import EvaluationReport
    from tracecause.storage.postgres import StorageConflict

    client, resources = api
    report = EvaluationReport(
        report_id=uuid4(),
        origin="synthetic",
        dataset_hash="a" * 64,
        split_hash="b" * 64,
        config_hash="c" * 64,
        model_digest="d" * 64,
        sample_size=0,
        metrics={"canonical.test.anomaly.hit_at_1": None},
        created_at=datetime.now(UTC),
        published_at=None,
    )
    assert client.get(f"/api/v1/evaluations/{report.report_id}").status_code == 404
    saved = publish(resources, report)
    assert publish(resources, report) == saved
    response = client.get(f"/api/v1/evaluations/{report.report_id}")
    assert response.status_code == 200, response.text
    assert response.json()["metrics"] == report.metrics
    assert client.get("/api/v1/evaluations").json()["items"]
    assert (
        "private_run_reference" not in response.text
        and str(resources.artifacts.root) not in response.text
    )
    with pytest.raises(StorageConflict):
        publish(resources, report.model_copy(update={"sample_size": 1}))
    with pytest.raises(ValueError, match="sanitized"):
        publish(
            resources,
            report.model_copy(update={"report_id": uuid4(), "metrics": {"secret-path": 0}}),
        )


def test_concurrent_keys_scope_checks_and_cancelled_cached_result(api):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta

    from tracecause.jobs.api_worker import JobCancelled
    from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
    from tracecause.storage.schema import JobRow, SnapshotRow

    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    body = request_for(manifest, episode)
    for changes in ({"question": "   "}, {"symptom_service_id": "a" * 64}):
        response = client.post(
            "/api/v1/investigations",
            json={**body, **changes},
            headers={**HEADERS, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 422, response.text
    second, _, _, _ = ready_episode(client, resources)
    response = client.post(
        "/api/v1/investigations",
        json={**body, "episode_id": second["episode_id"]},
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 404
    with resources.factory.begin() as session:
        snapshot = session.get(SnapshotRow, UUID(episode["active_snapshot_id"]))
        snapshot.status = "TOMBSTONED"
    try:
        assert client.get("/health/ready").status_code == 503
        assert (
            client.post(
                "/api/v1/investigations",
                json=body,
                headers={**HEADERS, "Idempotency-Key": str(uuid4())},
            ).status_code
            == 409
        )
    finally:
        with resources.factory.begin() as session:
            session.get(SnapshotRow, UUID(episode["active_snapshot_id"])).status = "READY"

    def post(_):
        return client.post(
            "/api/v1/investigations",
            json=body,
            headers={**HEADERS, "Idempotency-Key": "concurrent-one"},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(post, range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["job_id"] for r in responses}) == 1
    job_id = UUID(responses[0].json()["job_id"])
    worker = ApiWorker(resources)
    # An abandoned lease is reclaimed by the real worker.
    worker.queue.claim("abandoned", kinds=("INVESTIGATE",), job_id=job_id)
    with resources.factory.begin() as session:
        row = session.get(JobRow, job_id)
        original_start = row.started_at
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert worker.run_once() == job_id
    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["status"] == "SUCCEEDED" and result["attempt_count"] == 2
    assert datetime.fromisoformat(result["started_at"].replace("Z", "+00:00")) == original_start
    assert result["finished_at"] is not None
    with resources.factory() as session:
        row = session.get(JobRow, job_id)
        from tracecause.contracts.models import InvestigationRequest

        request = InvestigationRequest.model_validate(row.payload["request"])
    cancelled = client.post(
        "/api/v1/investigations",
        json=body,
        headers={**HEADERS, "Idempotency-Key": "cancel-cached"},
    ).json()["job_id"]
    claimed = worker.queue.claim(worker.worker_id, kinds=("INVESTIGATE",), job_id=UUID(cancelled))
    assert claimed is not None
    worker.queue.cancel(claimed.id)
    with pytest.raises(JobCancelled):
        resources.investigations.investigate(
            DEFAULT_WORKSPACE_ID,
            request,
            publication_guard=lambda session, result: worker.guard(session, claimed.id, result),
        )
    worker.queue.fail(claimed.id, worker.worker_id, "cancelled", transient=False)
    assert client.get(f"/api/v1/jobs/{cancelled}").json()["status"] == "CANCELLED"


def test_loopback_http_with_separate_durable_worker(api, tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time

    import httpx

    _, resources = api
    database = resources.factory.kw["bind"].url.database
    token_file = tmp_path / "operator-token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    env = {
        **os.environ,
        "TRACECAUSE_PUBLIC_READ_ONLY": "false",
        "TRACECAUSE_OPERATOR_TOKEN_FILE": str(token_file),
        "TRACECAUSE_PG_HOST": "127.0.0.1",
        "TRACECAUSE_PG_PORT": "15432",
        "TRACECAUSE_PG_DATABASE": database,
        "TRACECAUSE_PG_USER": "postgres",
        "TRACECAUSE_PG_PASSWORD_FILE": str(ROOT / ".secrets/postgres_admin_password"),
        "TRACECAUSE_NEO4J_URI": "bolt://127.0.0.1:17687",
        "TRACECAUSE_NEO4J_AUTH_FILE": str(ROOT / ".secrets/neo4j_auth"),
        "TRACECAUSE_ARTIFACT_ROOT": str(resources.artifacts.root),
        "TRACECAUSE_STAGING_ROOT": str(resources.staging),
    }
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (tmp_path / "api.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tracecause.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20) as client:
                for _ in range(100):
                    assert process.poll() is None, "API exited before readiness"
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                else:
                    pytest.fail("API did not become ready")
                manifest, files = bundle()
                submitted = client.post(
                    "/api/v1/imports",
                    data={"manifest": json.dumps(manifest)},
                    files=files,
                    headers={**HEADERS, "Idempotency-Key": "tcp-import"},
                )
                assert submitted.status_code == 202, submitted.text

                def run_worker():
                    completed = subprocess.run(
                        [sys.executable, "-m", "tracecause.jobs.api_worker", "--once"],
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    assert completed.returncode == 0, completed.stderr

                run_worker()
                job = client.get("/api/v1/jobs/" + submitted.json()["job_id"]).json()
                assert job["status"] == "SUCCEEDED", job
                episode = client.get("/api/v1/episodes/" + manifest["episode_id"]).json()
                submitted = client.post(
                    "/api/v1/investigations",
                    json=request_for(manifest, episode),
                    headers={**HEADERS, "Idempotency-Key": "tcp-investigate"},
                )
                assert submitted.status_code == 202, submitted.text
                run_worker()
                view = client.get(
                    "/api/v1/investigations/" + submitted.json()["investigation_id"]
                ).json()
                assert view["result"] is not None, view
                assert view["result"]["evidence"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_upload_caps_missing_baseline_and_cleanup(api):
    from tracecause.api.uploads import FILE_LIMIT

    client, resources = api
    manifest, files = bundle()
    before = set(resources.staging.iterdir())
    manifest["sources"][0]["bytes"] = FILE_LIMIT + 1
    result = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 413
    assert set(resources.staging.iterdir()) == before
    manifest, files = bundle()
    manifest["baseline_interval"] = None
    result = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 422
    result = client.post(
        "/api/v1/imports",
        content=b"not multipart",
        headers=HEADERS,
    )
    assert result.status_code == 422
    assert set(resources.staging.iterdir()) == before


def test_deep_json_and_cross_origin_cookie_auth_are_rejected(api):
    client, _ = api
    deep = b'{"question":' + b"[" * 1000 + b"0" + b"]" * 1000 + b"}"
    response = client.post(
        "/api/v1/investigations",
        content=deep,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "json_too_deep"
    response = client.options(
        "/api/v1/investigations",
        headers={
            "Origin": "https://untrusted.invalid",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert "access-control-allow-origin" not in response.headers
    response = client.get("/api/v1/auth/check", headers={"Cookie": f"Authorization=Bearer {TOKEN}"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_sigkill_worker_and_graph_builder_recover_without_replacing_valid_snapshot(api, tmp_path):
    import os
    import subprocess
    import sys
    import time
    from datetime import UTC, datetime

    from tests.unit.test_security_recovery import wait_file

    from tracecause.graph.projection import build_snapshot
    from tracecause.storage.schema import JobRow, SnapshotRow

    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    with resources.factory() as session:
        database = session.bind.url.database
    token_file = tmp_path / "operator-token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    env = {
        **os.environ,
        "TRACECAUSE_PUBLIC_READ_ONLY": "false",
        "TRACECAUSE_OPERATOR_TOKEN_FILE": str(token_file),
        "TRACECAUSE_PG_HOST": "127.0.0.1",
        "TRACECAUSE_PG_PORT": "15432",
        "TRACECAUSE_PG_DATABASE": database,
        "TRACECAUSE_PG_USER": "postgres",
        "TRACECAUSE_PG_PASSWORD_FILE": str(ROOT / ".secrets/postgres_admin_password"),
        "TRACECAUSE_NEO4J_URI": "bolt://127.0.0.1:17687",
        "TRACECAUSE_NEO4J_AUTH_FILE": str(ROOT / ".secrets/neo4j_auth"),
        "TRACECAUSE_ARTIFACT_ROOT": str(resources.artifacts.root),
        "TRACECAUSE_STAGING_ROOT": str(resources.staging),
    }
    submitted = client.post(
        "/api/v1/investigations",
        json=request_for(manifest, episode),
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert submitted.status_code == 202
    job_id = UUID(submitted.json()["job_id"])
    worker_ready = tmp_path / "worker-ready"
    code = """
import signal, sys
from pathlib import Path
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.jobs.api_worker import ApiWorker
original = ApiWorker.checkpoint
def checkpoint(self, job_id, stage):
    original(self, job_id, stage)
    if stage == 'investigating':
        Path(sys.argv[1]).touch()
        signal.pause()
ApiWorker.checkpoint = checkpoint
r = open_resources(load_settings(Path('configs/defaults.yaml')))
ApiWorker(r).run_once()
"""
    with (tmp_path / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(worker_ready)],
            env=env,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_file(worker_ready, process)
            process.kill()
            process.wait(timeout=5)
            assert process.returncode == -9
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    with resources.factory() as session:
        row = session.get(JobRow, job_id)
        assert row.status == "RUNNING" and row.checkpoint == "investigating"
        lease_until = row.lease_until
    # Let the actual persisted lease expire; no SQL clock acceleration.
    remaining = max(0, (lease_until - datetime.now(UTC)).total_seconds())
    time.sleep(remaining + 0.1)
    assert ApiWorker(resources).run_once() == job_id
    recovered = client.get(f"/api/v1/jobs/{job_id}").json()
    assert recovered["status"] == "SUCCEEDED"
    with resources.factory() as session:
        assert session.get(JobRow, job_id).attempts == 2

    graph_ready = tmp_path / "graph-ready"
    snapshot_id = uuid4()
    code = """
import signal, sys
from pathlib import Path
from uuid import UUID
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
import tracecause.graph.projection as projection
original = projection.project_material
def interrupted(driver, snapshot_id, material, **kwargs):
    try:
        original(driver, snapshot_id, material, fail_after='services')
    except projection.GraphBuildError:
        Path(sys.argv[1]).touch()
        signal.pause()
projection.project_material = interrupted
r = open_resources(load_settings(Path('configs/defaults.yaml')))
projection.build_snapshot(r.factory, r.driver, workspace_id=DEFAULT_WORKSPACE_ID,
    episode_id=UUID(sys.argv[2]), snapshot_id=UUID(sys.argv[3]), artifacts=r.artifacts)
"""
    with (tmp_path / "graph.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(graph_ready),
                manifest["episode_id"],
                str(snapshot_id),
            ],
            env=env,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_file(graph_ready, process)
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    with resources.factory() as session:
        crashed = session.get(SnapshotRow, snapshot_id)
        assert crashed.status == "BUILDING"
        workspace = crashed.workspace_id
        episode_id = crashed.episode_id
        assert (
            str(session.get(EpisodeRow, episode_id).active_snapshot_id)
            == episode["active_snapshot_id"]
        )
    # The original snapshot still serves scoped reads after the partial build died.
    assert client.get(f"/api/v1/episodes/{episode_id}").status_code == 200
    rebuilt = build_snapshot(
        resources.factory,
        resources.driver,
        workspace_id=workspace,
        episode_id=episode_id,
        snapshot_id=snapshot_id,
        artifacts=resources.artifacts,
    )
    assert rebuilt.status == "READY" and rebuilt.snapshot_id == snapshot_id


@pytest.mark.parametrize("dependency", ["postgres", "neo4j"])
def test_actual_unreachable_dependency_has_safe_readiness_error(api, tmp_path, dependency):
    import socket

    from neo4j import GraphDatabase
    from sqlalchemy.orm import sessionmaker

    from tracecause.storage.postgres import make_engine

    _, original = api
    # Reserve an unlistening loopback socket: real drivers encounter refusal,
    # and no unrelated running database is stopped.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        with original.factory() as session:
            url = session.bind.url
        engine = make_engine(url.set(port=port)) if dependency == "postgres" else None
        driver = (
            GraphDatabase.driver(
                f"bolt://127.0.0.1:{port}",
                auth=("neo4j", "seeded-credential-never-echo"),
                connection_timeout=1,
                connection_acquisition_timeout=1,
                max_transaction_retry_time=1,
            )
            if dependency == "neo4j"
            else original.driver
        )
        resources = Resources(
            original.settings,
            sessionmaker(engine, expire_on_commit=False) if engine else original.factory,
            driver,
            original.artifacts,
        )
        try:
            with TestClient(
                create_app(original.settings, resources), raise_server_exceptions=False
            ) as client:
                assert client.get("/health/live").status_code == 200
                response = client.get("/health/ready")
                assert response.status_code == 503, response.text
                assert response.json()["error"]["retryable"]
                for forbidden in ("seeded-credential", "postgresql", "bolt://", "Traceback"):
                    assert forbidden not in response.text
        finally:
            if engine:
                engine.dispose()
            if driver is not original.driver:
                driver.close()


def test_query_injection_is_data_and_does_not_mutate_graph(api):
    client, resources = api
    manifest, _, episode, _ = ready_episode(client, resources)
    with resources.driver.session(database="neo4j") as graph:
        before = graph.run(
            "MATCH (n {episode_id:$id}) RETURN count(n) AS n", id=manifest["episode_id"]
        ).single()["n"]
    request = request_for(manifest, episode)
    request["question"] = "MATCH (n) DETACH DELETE n; OR *:* )) ' UNION SELECT secret --"
    result = client.post(
        "/api/v1/investigations", json=request, headers={**HEADERS, "Idempotency-Key": str(uuid4())}
    )
    assert result.status_code == 202
    assert ApiWorker(resources).run_once() == UUID(result.json()["job_id"])
    assert client.get(f"/api/v1/jobs/{result.json()['job_id']}").json()["status"] == "SUCCEEDED"
    with resources.driver.session(database="neo4j") as graph:
        after = graph.run(
            "MATCH (n {episode_id:$id}) RETURN count(n) AS n", id=manifest["episode_id"]
        ).single()["n"]
    assert before == after and before > 0


def test_internal_metrics_require_operator_and_do_not_label_raw_paths(api):
    client, resources = api
    assert client.get("/internal/metrics").status_code == 401
    for index in range(4):
        client.get(f"/not-a-route/private-{index}")
    result = client.get("/internal/metrics", headers=HEADERS)
    assert result.status_code == 200
    assert "tracecause_http_seconds_count" in result.text
    assert "tracecause_jobs{" in result.text
    assert "private-" not in result.text and TOKEN not in result.text
    public = resources.settings.model_copy(update={"public_read_only": True})
    with TestClient(create_app(public, resources)) as reader:
        assert reader.get("/internal/metrics", headers=HEADERS).status_code == 403


def test_verified_knowledge_paths_sources_and_tampering(api):
    """Real PostgreSQL/Neo4j: authored checks join directed calls with source proof."""
    import hashlib

    client, resources = api
    manifest, files = bundle()
    for name in ("demo-doc-01.md", "demo-doc-05.md", "demo-doc-17.md"):
        raw = (ROOT / "data/runbooks/demo" / name).read_bytes()
        manifest["sources"].append(
            dict(
                logical_name=name,
                kind="markdown",
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                published_at=None,
            )
        )
        files.append(("files", (name, raw, "text/markdown")))
    submitted = client.post(
        "/api/v1/imports",
        data={"manifest": json.dumps(manifest)},
        files=files,
        headers={**HEADERS, "Idempotency-Key": str(uuid4())},
    )
    assert submitted.status_code == 202, submitted.text
    ApiWorker(resources).run_once()
    job = client.get("/api/v1/jobs/" + submitted.json()["job_id"]).json()
    assert job["status"] == "SUCCEEDED", job
    scope = dict(
        episode_id=manifest["episode_id"],
        snapshot_id=job["result_id"],
        as_of=manifest["event_interval"]["end"],
        temporal_mode="event_time_replay",
    )
    response = client.get("/api/v1/knowledge", params=scope)
    assert response.status_code == 200, response.text
    knowledge = response.json()
    assert {n["kind"] for n in knowledge["nodes"]} == {
        "service",
        "observation",
        "document",
        "chunk",
        "assertion",
    }
    evidence_scope = {k: v for k, v in scope.items() if k != "episode_id"}
    for identifier in {eid for edge in knowledge["edges"] for eid in edge["evidence_ids"]}:
        opened = client.get("/api/v1/evidence/" + identifier, params=evidence_scope)
        assert opened.status_code == 200, opened.text
    graph = client.get("/api/v1/graph", params=scope).json()
    storefront = next(n["service_id"] for n in graph["nodes"] if n["display_name"] == "storefront")
    checked = client.get(
        "/api/v1/knowledge/checks", params={**scope, "symptom_service_id": storefront}
    )
    assert checked.status_code == 200, checked.text
    checks = checked.json()["checks"]
    assert checks and any(c["call_edges"] for c in checks)
    for check in checks:
        assert check["service_ids"][0] == storefront
        assert len(check["call_edges"]) <= 3
        opened = client.get("/api/v1/evidence/" + check["evidence_id"], params=evidence_scope)
        assert check["quote"] in opened.text
    assert (
        client.get("/api/v1/knowledge", params={**scope, "episode_id": str(uuid4())}).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/knowledge/checks", params={**scope, "symptom_service_id": "0" * 64}
        ).status_code
        == 404
    )
    # Only the disposable test snapshot is modified; the authoritative source is unchanged.
    with resources.driver.session(database="neo4j") as session:
        original = session.run(
            "MATCH (a:Assertion {snapshot_id:$sid}) "
            "RETURN a.assertion_id AS id,a.quote AS quote LIMIT 1",
            sid=job["result_id"],
        ).single()
        assert original
        try:
            session.run(
                "MATCH (a:Assertion {snapshot_id:$sid,assertion_id:$aid}) SET a.quote=$quote",
                sid=job["result_id"],
                aid=original["id"],
                quote="Forged check",
            ).consume()
            denied = client.get("/api/v1/knowledge", params=scope)
            assert denied.status_code == 409, denied.text
            assert "Forged check" not in denied.text
        finally:
            session.run(
                "MATCH (a:Assertion {snapshot_id:$sid,assertion_id:$aid}) SET a.quote=$quote",
                sid=job["result_id"],
                aid=original["id"],
                quote=original["quote"],
            ).consume()
    assert client.get("/api/v1/knowledge", params=scope).status_code == 200
