"""Real PostgreSQL import, lease and artifact integration tests."""

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg import connect, sql
from pydantic import SecretStr
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.cli import main as cli_main
from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.models import (
    GenerationMode,
    InvestigationRequest,
    InvestigationResult,
    Narrative,
    RankingMode,
    RetrievalMode,
    TemporalMode,
)
from tracecause.documents.persistence import ingest_corpus
from tracecause.graph.maintenance import reader_lease, retire_snapshot
from tracecause.graph.maintenance import reconciliation_inventory as graph_inventory
from tracecause.graph.projection import GraphBuildError, build_snapshot, load_graph_material
from tracecause.graph.queries import GraphReader, GraphReadError
from tracecause.graph.schema import driver_from_auth_file, migrate_graph
from tracecause.ingestion.identity import AliasRegistry
from tracecause.investigation.generation import GenerationOutput
from tracecause.investigation.ranking import generate_candidates, is_anomalous, score_candidates
from tracecause.investigation.service import InvestigationService
from tracecause.investigation.templates import ns_datetime
from tracecause.investigation.validation import InvestigationValidationError, validate_result
from tracecause.jobs.imports import ImportValidationError, ImportWorker, submit_import
from tracecause.jobs.queue import JobConflict, JobQueue
from tracecause.retrieval.embeddings import MiniLMEmbedder
from tracecause.retrieval.engine import RetrievalRequest, corpus_from_reader, retrieve
from tracecause.storage.artifacts import ArtifactError, ArtifactRepository
from tracecause.storage.maintenance import (
    backup_metadata,
    delete_unreferenced_artifact,
    reconciliation_inventory,
)
from tracecause.storage.postgres import (
    DEFAULT_WORKSPACE_ID,
    StorageConflict,
    database_url,
    make_engine,
    sessions,
)
from tracecause.storage.schema import (
    ChunkRow,
    DocumentRow,
    EpisodeRow,
    JobRow,
    ObservationRow,
    ObservationSpanRow,
    SnapshotRow,
    SourceArtifactRow,
    SpanRow,
)
from tracecause.telemetry.persistence import (
    compute_persisted_signals,
    inspect_signals,
    materialize_signals,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def test_database_name():
    name = f"tracecause_it_{uuid4().hex[:12]}"
    secret = (ROOT / ".secrets/postgres_admin_password").read_text().strip()
    with connect(
        host="127.0.0.1",
        port=15432,
        dbname="postgres",
        user="postgres",
        password=secret,
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield name
    with connect(
        host="127.0.0.1",
        port=15432,
        dbname="postgres",
        user="postgres",
        password=secret,
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def connection_url(test_database_name):
    return database_url(
        host="127.0.0.1",
        port=15432,
        database=test_database_name,
        user="postgres",
        password_file=ROOT / ".secrets/postgres_admin_password",
    )


@pytest.fixture(scope="module")
def factory(connection_url):
    engine = make_engine(connection_url)
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield sessions(engine)
    engine.dispose()


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactRepository:
    return ArtifactRepository(tmp_path / "artifacts")


@pytest.fixture
def episode_dir(tmp_path: Path) -> tuple[Path, UUID]:
    root = ROOT / "data/fixtures/smoke"
    original_id = json.loads((root / "index.json").read_text())["episodes"][0]["episode_id"]
    directory = tmp_path / "episode"
    shutil.copytree(root / original_id, directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    episode_id = uuid4()
    manifest["episode_id"] = str(episode_id)
    manifest_path.write_text(json.dumps(manifest))
    return directory, episode_id


def test_migration_is_repeatable_and_tables_exist(connection_url, factory) -> None:
    engine = make_engine(connection_url)
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    names = set(inspect(engine).get_table_names())
    assert {
        "workspaces",
        "episodes",
        "spans",
        "jobs",
        "source_artifacts",
        "snapshots",
        "observation_spans",
        "chunks",
        "evaluation_reports",
    } <= names
    engine.dispose()


def test_capture_baseline_import_repairs_old_boundary_without_changing_job(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    end = datetime.fromisoformat(manifest["baseline_interval"]["end"].replace("Z", "+00:00"))
    end += timedelta(seconds=2, microseconds=500000)
    manifest["baseline_interval"]["end"] = end.isoformat()
    manifest_path.write_text(json.dumps(manifest))
    raw_ns = int(end.timestamp()) * 1_000_000_000 + end.microsecond * 1000
    expected = raw_ns // 10_000_000_000 * 10_000_000_000
    first = submit_import(factory, artifacts, directory, idempotency_key="capture-boundary")
    with factory.begin() as session:
        row = session.get(EpisodeRow, episode_id)
        assert row is not None and row.analysis_start_ns == expected
        row.analysis_start_ns = expected + 10_000_000_000
    replay = submit_import(factory, artifacts, directory, idempotency_key="capture-boundary")
    assert replay.job_id == first.job_id
    with factory() as session:
        row = session.get(EpisodeRow, episode_id)
        assert row is not None and row.analysis_start_ns == expected
        assert row.analysis_start_ns < raw_ns
    assert ImportWorker(factory, artifacts, "capture-boundary-worker").run_once() == first.job_id
    event_end = datetime.fromisoformat(manifest["event_interval"]["end"].replace("Z", "+00:00"))
    cutoff = int(event_end.timestamp()) * 1_000_000_000
    for boundary in (expected + 10_000_000_000, expected):
        materialize_signals(
            factory,
            artifacts,
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            investigation_start_ns=boundary,
            as_of_ns=cutoff,
        )
    with factory() as session:
        observations = session.scalars(
            select(ObservationRow).where(ObservationRow.episode_id == episode_id)
        ).all()
        stale = {
            str(item.id)
            for item in observations
            if item.attributes.get("signal", {}).get("baseline_end_ns") == expected + 10_000_000_000
        }
        current = {
            str(item.id)
            for item in observations
            if item.attributes.get("signal", {}).get("baseline_end_ns") == expected
        }
        material = load_graph_material(
            session, DEFAULT_WORKSPACE_ID, episode_id, artifacts=artifacts
        )
    published = {item["source_id"] for item in material.observations}
    assert stale and current
    assert not stale & published
    assert current <= published


def test_import_idempotence_and_separate_worker_process(
    connection_url, factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    first = submit_import(factory, artifacts, directory, idempotency_key="same-key")
    second = submit_import(factory, artifacts, directory, idempotency_key="same-key")
    assert first.job_id == second.job_id
    assert first.status == "QUEUED"
    with factory() as session:
        assert session.get(EpisodeRow, episode_id).ingestion_status == "QUEUED"
        assert (
            session.scalar(
                select(func.count()).select_from(SpanRow).where(SpanRow.episode_id == episode_id)
            )
            == 0
        )
    # Simulate a fresh worker process with a new SQLAlchemy engine/pool.
    second_engine = make_engine(connection_url)
    try:
        worker = ImportWorker(sessions(second_engine), artifacts, f"worker-{uuid4()}")
        assert worker.run_once() == first.job_id
    finally:
        second_engine.dispose()
    with factory() as session:
        job = session.get(JobRow, first.job_id)
        episode = session.get(EpisodeRow, episode_id)
        assert job.status == "SUCCEEDED" and job.checkpoint == "spans_persisted"
        assert episode.ingestion_status == "IMPORTED" and episode.active_snapshot_id is None
        stored = session.scalars(select(SpanRow).where(SpanRow.episode_id == episode_id)).all()
        assert len(stored) == 71
        artifact_spans = {
            (item["trace_id"], item["span_id"])
            for line in artifacts.iter_lines(first.normalized_artifact_hash)
            for item in [json.loads(line)]
        }
        assert {(row.trace_id, row.span_id) for row in stored} == artifact_spans
    assert (
        submit_import(factory, artifacts, directory, idempotency_key="same-key").job_id
        == first.job_id
    )
    assert (
        submit_import(factory, artifacts, directory, idempotency_key="other-key").job_id
        == first.job_id
    )


def test_same_key_different_manifest_conflicts(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, _ = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="same-key")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["display_name"] = "Changed metadata"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(StorageConflict, match="manifest"):
        submit_import(factory, artifacts, directory, idempotency_key="same-key")
    JobQueue(factory).cancel(submission.job_id)


def test_same_key_with_different_job_payload_conflicts_in_queue(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="payload-key")
    with pytest.raises(JobConflict, match="different input"):
        with factory.begin() as session:
            JobQueue(factory).enqueue_import(
                session,
                workspace_id=session.get(EpisodeRow, episode_id).workspace_id,
                episode_id=episode_id,
                idempotency_key="payload-key",
                input_hash="f" * 64,
                fingerprint="e" * 64,
                payload={"different": True},
            )
    JobQueue(factory).cancel(submission.job_id)


def test_metadata_and_idempotency_secret_are_not_persisted_raw(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["display_name"] = "jane@example.com"
    manifest_path.write_text(json.dumps(manifest))
    secret_key = "Bearer abcDEF123456789012345678901234"
    submission = submit_import(factory, artifacts, directory, idempotency_key=secret_key)
    with factory() as session:
        episode = session.get(EpisodeRow, episode_id)
        job = session.get(JobRow, submission.job_id)
        assert episode.display_name == "<redacted-episode>"
        assert secret_key not in job.idempotency_key
        assert len(job.idempotency_key) == 64
    JobQueue(factory).cancel(submission.job_id)


def test_concurrent_same_key_submits_one_job(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(
            submit_import, factory, artifacts, directory, idempotency_key="concurrent"
        )
        future_b = executor.submit(
            submit_import, factory, artifacts, directory, idempotency_key="concurrent"
        )
        result_a, result_b = future_a.result(), future_b.result()
    assert result_a.job_id == result_b.job_id
    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(JobRow).where(JobRow.episode_id == episode_id)
            )
            == 1
        )
    JobQueue(factory).cancel(result_a.job_id)


def test_expired_lease_reclaims_checkpoint_and_processes_once(
    connection_url, factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="lease")
    queue = JobQueue(factory)
    claimed = queue.claim("crashed-worker")
    assert claimed is not None and claimed.id == submission.job_id
    queue.checkpoint(claimed.id, "crashed-worker", "validated")
    restarted_engine = make_engine(connection_url)
    try:
        restarted_factory = sessions(restarted_engine)
        resumed = JobQueue(restarted_factory).claim(
            "resumed-worker", now=claimed.lease_until + timedelta(seconds=1)
        )
        assert resumed is not None and resumed.id == claimed.id and resumed.attempts == 2
        worker = ImportWorker(restarted_factory, artifacts, "resumed-worker")
        assert worker.process_claimed(resumed.id) == 71
    finally:
        restarted_engine.dispose()
    with factory() as session:
        assert session.get(JobRow, resumed.id).status == "SUCCEEDED"
        assert (
            session.scalar(
                select(func.count()).select_from(SpanRow).where(SpanRow.episode_id == episode_id)
            )
            == 71
        )


def test_cancel_after_claim_prevents_publication(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="cancel")
    queue = JobQueue(factory)
    claimed = queue.claim("cancel-worker")
    assert claimed is not None and claimed.id == submission.job_id
    assert queue.cancel(claimed.id) == "RUNNING"
    worker = ImportWorker(factory, artifacts, "cancel-worker")
    assert worker.process_claimed(claimed.id) == 0
    with factory() as session:
        assert session.get(JobRow, claimed.id).status == "CANCELLED"
        assert session.get(EpisodeRow, episode_id).active_snapshot_id is None
        assert (
            session.scalar(
                select(func.count()).select_from(SpanRow).where(SpanRow.episode_id == episode_id)
            )
            == 0
        )


def test_artifact_write_failure_leaves_no_database_rows(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir, monkeypatch
) -> None:
    directory, episode_id = episode_dir

    def fail_write(_: bytes):
        raise ArtifactError("injected_write_failure")

    monkeypatch.setattr(artifacts, "put_bytes", fail_write)
    with pytest.raises(ArtifactError, match="injected_write_failure"):
        submit_import(factory, artifacts, directory, idempotency_key="write-failure")
    with factory() as session:
        assert session.get(EpisodeRow, episode_id) is None


def test_rejected_records_have_safe_durable_diagnostics(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    source_path = directory / "traces.jsonl"
    source = source_path.read_bytes() + b'{"secret":"do-not-report",\n'
    source_path.write_bytes(source)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["sha256"] = hashlib.sha256(source).hexdigest()
    manifest["sources"][0]["bytes"] = len(source)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ImportValidationError) as caught:
        submit_import(factory, artifacts, directory, idempotency_key="strict-rejection")
    assert caught.value.code == "source_records_rejected"
    assert caught.value.rejected_count == 1
    assert caught.value.diagnostics[0].code == "malformed_json"
    assert "do-not-report" not in json.dumps(
        [item.model_dump(mode="json") for item in caught.value.diagnostics]
    )
    with factory() as session:
        assert session.get(EpisodeRow, episode_id) is None
    submission = submit_import(
        factory, artifacts, directory, idempotency_key="partial-rejection", allow_partial=True
    )
    assert submission.rejected_count == 1
    with factory() as session:
        job = session.get(JobRow, submission.job_id)
        assert job.payload["diagnostics"][0]["code"] == "malformed_json"
        assert "do-not-report" not in json.dumps(job.payload)
    JobQueue(factory).cancel(submission.job_id)


def test_tampered_artifact_fails_without_partial_import(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="tampered")
    path = (
        artifacts.root
        / f"sha256/{submission.normalized_artifact_hash[:2]}/{submission.normalized_artifact_hash}"
    )
    original = path.read_bytes()
    path.write_bytes(original + b"corruption")
    worker = ImportWorker(factory, artifacts, "tamper-worker")
    assert worker.run_once() == submission.job_id
    with factory() as session:
        assert session.get(JobRow, submission.job_id).status == "FAILED"
        assert session.get(EpisodeRow, episode_id).active_snapshot_id is None
        assert (
            session.scalar(
                select(func.count()).select_from(SpanRow).where(SpanRow.episode_id == episode_id)
            )
            == 0
        )
    path.write_bytes(original)
    assert JobQueue(factory).retry(submission.job_id) == "QUEUED"
    assert ImportWorker(factory, artifacts, "recovery-worker").run_once() == submission.job_id
    with factory() as session:
        assert session.get(JobRow, submission.job_id).status == "SUCCEEDED"
        assert session.get(EpisodeRow, episode_id).ingestion_status == "IMPORTED"


def test_retry_wait_is_bounded_and_reclaimable(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, _ = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="retry")
    queue = JobQueue(factory)
    first = queue.claim("retry-worker")
    assert first is not None and first.id == submission.job_id
    assert (
        queue.fail(first.id, "retry-worker", "temporary_dependency", transient=True) == "RETRY_WAIT"
    )
    with factory() as session:
        retry_at = session.get(JobRow, first.id).next_retry_at
    assert retry_at is not None
    second = queue.claim("retry-worker-2", now=retry_at + timedelta(seconds=1))
    assert second is not None and second.attempts == 2
    queue.cancel(second.id)
    assert ImportWorker(factory, artifacts, "retry-worker-2").process_claimed(second.id) == 0


def test_heartbeat_and_retry_limit_are_durable(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="retry-limit")
    queue = JobQueue(factory)
    for attempt in range(1, 4):
        claimed = queue.claim(
            f"retry-{attempt}", now=datetime.now(UTC) + timedelta(minutes=attempt)
        )
        assert claimed is not None and claimed.id == submission.job_id
        assert claimed.attempts == attempt
        assert queue.heartbeat(claimed.id, f"retry-{attempt}")
        status = queue.fail(claimed.id, f"retry-{attempt}", "temporary_dependency", transient=True)
        assert status == ("FAILED" if attempt == 3 else "RETRY_WAIT")
    with factory() as session:
        row = session.get(JobRow, submission.job_id)
        assert row.status == "FAILED" and row.safe_error == "temporary_dependency"
        assert session.get(EpisodeRow, episode_id).ingestion_status == "FAILED"


def test_queued_cancel_and_exclusive_claim(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="exclusive")
    queue = JobQueue(factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(queue.claim, "worker-a")
        second = executor.submit(queue.claim, "worker-b")
        claims = [first.result(), second.result()]
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1 and claimed[0].id == submission.job_id
    queue.cancel(submission.job_id)
    assert (
        ImportWorker(factory, artifacts, claimed[0].lease_owner).process_claimed(submission.job_id)
        == 0
    )
    with factory() as session:
        assert session.get(JobRow, submission.job_id).status == "CANCELLED"
        assert session.get(EpisodeRow, episode_id).ingestion_status == "CANCELLED"


def test_cancel_before_claim_and_expired_attempts(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="cancel-queued")
    queue = JobQueue(factory)
    assert queue.cancel(submission.job_id) == "CANCELLED"
    with factory() as session:
        assert session.get(EpisodeRow, episode_id).ingestion_status == "CANCELLED"
    assert queue.claim("unused-worker") is None


def test_exhausted_crashed_leases_fail_without_ready_episode(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="crash-limit")
    queue = JobQueue(factory)
    instant = datetime.now(UTC)
    for attempt in range(1, 4):
        claimed = queue.claim(f"crashed-{attempt}", now=instant)
        assert claimed is not None and claimed.id == submission.job_id
        assert claimed.attempts == attempt
        instant = claimed.lease_until + timedelta(seconds=1)
    assert queue.claim("unused-worker", now=instant) is None
    with factory() as session:
        job = session.get(JobRow, submission.job_id)
        episode = session.get(EpisodeRow, episode_id)
        assert job.status == "FAILED" and job.safe_error == "lease_attempts_exhausted"
        assert episode.ingestion_status == "FAILED" and episode.active_snapshot_id is None


def test_reference_checked_cleanup_and_metadata_backup(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="cleanup")
    with pytest.raises(ValueError, match="referenced"):
        delete_unreferenced_artifact(
            factory, artifacts, submission.normalized_artifact_hash, dry_run=False
        )
    orphan = artifacts.put_bytes(b"test-owned backup metadata")
    assert (
        orphan.content_hash
        in reconciliation_inventory(factory, artifacts)["unregistered_artifacts"]
    )
    with factory.begin() as session:
        session.add(
            SourceArtifactRow(
                content_hash=orphan.content_hash,
                workspace_id=session.get(EpisodeRow, episode_id).workspace_id,
                kind="backup_metadata",
                byte_size=orphan.byte_size,
                relative_storage_key=orphan.relative_key,
                schema_version="1.0",
                redaction_version="none",
            )
        )
    assert (
        delete_unreferenced_artifact(factory, artifacts, orphan.content_hash, dry_run=True) is False
    )
    assert artifacts.verify(orphan.content_hash) == orphan.byte_size
    assert delete_unreferenced_artifact(factory, artifacts, orphan.content_hash, dry_run=False)
    with factory() as session:
        assert session.get(SourceArtifactRow, orphan.content_hash) is None
        metadata = backup_metadata(session)
        assert metadata["schema_revision"] == "0007"
        assert metadata["row_counts"]["episodes"] >= 1
    JobQueue(factory).cancel(submission.job_id)


def test_persisted_conflicting_span_is_not_overwritten(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="replay")
    worker = ImportWorker(factory, artifacts, "first-worker")
    assert worker.run_once() == submission.job_id
    with factory.begin() as session:
        first_span = session.scalar(
            select(SpanRow).where(SpanRow.episode_id == episode_id).limit(1)
        )
        assert first_span is not None
        first_span.normalized_hash = "f" * 64
        job = session.get(JobRow, submission.job_id)
        job.status = "QUEUED"
        job.attempts = 0
        job.checkpoint = "staged"
    second_worker = ImportWorker(factory, artifacts, "second-worker")
    assert second_worker.run_once() == submission.job_id
    with factory() as session:
        assert session.get(JobRow, submission.job_id).status == "FAILED"
        assert (
            session.scalar(
                select(func.count()).select_from(SpanRow).where(SpanRow.episode_id == episode_id)
            )
            == 71
        )


def test_signal_materialization_is_idempotent_and_supports_resolve(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="signals")
    assert ImportWorker(factory, artifacts, "signals-worker").run_once() == submission.job_id
    manifest = json.loads((directory / "manifest.json").read_text())
    start = (
        int(
            datetime.fromisoformat(
                manifest["baseline_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    cutoff = (
        int(
            datetime.fromisoformat(
                manifest["event_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    # Availability is an episode capability, including windows with no selected spans.
    for requested_start, requested_cutoff in ((start, cutoff), (start - 600_000_000_000,) * 2):
        with pytest.raises(ValueError, match="per-record availability is absent"):
            materialize_signals(
                factory,
                artifacts,
                workspace_id=UUID(manifest["workspace_id"]),
                episode_id=episode_id,
                investigation_start_ns=requested_start,
                as_of_ns=requested_cutoff,
                mode="available",
            )
        with pytest.raises(ValueError, match="per-record availability is absent"):
            compute_persisted_signals(
                factory,
                workspace_id=UUID(manifest["workspace_id"]),
                episode_id=episode_id,
                as_of_ns=requested_cutoff,
                mode="available",
            )
    first = materialize_signals(
        factory,
        artifacts,
        workspace_id=UUID(manifest["workspace_id"]),
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    second = materialize_signals(
        factory,
        artifacts,
        workspace_id=UUID(manifest["workspace_id"]),
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    assert first == second and first.observation_count > 0 and first.signal_count > 0
    document = inspect_signals(
        factory,
        artifacts,
        workspace_id=UUID(manifest["workspace_id"]),
        episode_id=episode_id,
        report_hash=first.report_hash,
    )
    assert document["input_hash"] == first.input_hash
    assert all("insufficient_baseline" in signal["flags"] for signal in document["signals"])
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ObservationRow)
                .where(ObservationRow.episode_id == episode_id)
            )
            == first.observation_count
        )
        supports = session.execute(
            select(ObservationSpanRow.trace_id, ObservationSpanRow.span_id).where(
                ObservationSpanRow.episode_id == episode_id
            )
        ).all()
        span_keys = set(
            session.execute(
                select(SpanRow.trace_id, SpanRow.span_id).where(SpanRow.episode_id == episode_id)
            ).all()
        )
        assert supports and set(supports) <= span_keys
    first_observation = document["observations"][0]
    assert (
        inspect_signals(
            factory,
            artifacts,
            workspace_id=UUID(manifest["workspace_id"]),
            episode_id=episode_id,
            report_hash=first.report_hash,
            observation_id=UUID(first_observation["id"]),
        )
        == first_observation
    )
    with pytest.raises(StorageConflict, match="outside scope"):
        inspect_signals(
            factory,
            artifacts,
            workspace_id=UUID("00000000-0000-4000-8000-000000000099"),
            episode_id=episode_id,
            report_hash=first.report_hash,
        )


def test_graph_snapshot_publication_failure_and_temporal_read(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    submission = submit_import(factory, artifacts, directory, idempotency_key="graph")
    assert ImportWorker(factory, artifacts, "graph-worker").run_once() == submission.job_id
    manifest = json.loads((directory / "manifest.json").read_text())
    workspace_id = UUID(manifest["workspace_id"])
    start = (
        int(
            datetime.fromisoformat(
                manifest["baseline_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    cutoff = (
        int(
            datetime.fromisoformat(
                manifest["event_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    materialize_signals(
        factory,
        artifacts,
        workspace_id=workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        snapshot_ids: list[UUID] = []
        try:
            assert migrate_graph(driver)["schema_version"] == "1.2"
            first = build_snapshot(
                factory, driver, workspace_id=workspace_id, episode_id=episode_id
            )
            snapshot_ids.append(first.snapshot_id)
            assert first.status == "READY" and first.calls > 0 and first.incidents > 0
            with factory() as session:
                assert session.get(EpisodeRow, episode_id).active_snapshot_id == first.snapshot_id
            with pytest.raises(GraphBuildError, match="injected"):
                build_snapshot(
                    factory,
                    driver,
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    fail_after="services",
                )
            with factory() as session:
                assert session.get(EpisodeRow, episode_id).active_snapshot_id == first.snapshot_id
                failed = session.scalars(
                    select(SnapshotRow).where(
                        SnapshotRow.episode_id == episode_id, SnapshotRow.status == "FAILED"
                    )
                ).all()
                assert (
                    len(failed) == 1 and failed[0].error_code == "projection_or_validation_failed"
                )
                snapshot_ids.append(failed[0].id)
            reader = GraphReader(factory, driver)
            calls = reader.eligible_calls(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=cutoff,
            )
            assert calls
            edge = calls[0]
            with factory() as session:
                first_support_end = min(
                    session.get(ObservationRow, observation_id).end_ns
                    for observation_id in edge.support_observation_ids
                )
            early = reader.eligible_calls(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=first_support_end - 1,
            )
            assert not any(
                item.caller_service_key == edge.caller_service_key
                and item.callee_service_key == edge.callee_service_key
                for item in early
            )
            assert (
                reader.neighbors(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    as_of_ns=cutoff,
                    service_key="x'}) MATCH (n) DETACH DELETE n //",
                )
                == ()
            )
            assert reader.support_paths(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=cutoff,
                source_service_key=edge.caller_service_key,
                target_service_key=edge.callee_service_key,
            )
            assert reader.subgraph(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=cutoff,
            )["calls"]
            observed = reader.observation(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=cutoff,
                observation_id=edge.support_observation_ids[0],
            )
            assert observed["supports"]
            with pytest.raises(GraphReadError):
                reader.eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=uuid4(),
                    as_of_ns=cutoff,
                )
            with pytest.raises(GraphReadError, match="availability"):
                reader.eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    as_of_ns=cutoff,
                    mode="available",
                )
            rebuilt = build_snapshot(
                factory, driver, workspace_id=workspace_id, episode_id=episode_id
            )
            snapshot_ids.append(rebuilt.snapshot_id)
            with factory() as session:
                assert session.get(EpisodeRow, episode_id).active_snapshot_id == rebuilt.snapshot_id
            assert (
                reader.eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    as_of_ns=cutoff,
                )
                == calls
            )
            inventory = graph_inventory(factory, driver)
            assert str(failed[0].id) in inventory["failed_partial_graph_snapshot_ids"]
            assert (
                str(first.snapshot_id) not in inventory["ready_missing_or_invalid_projection_ids"]
            )
            plan = retire_snapshot(
                factory,
                driver,
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
            )
            assert plan.dry_run and plan.eligible and plan.graph_node_count > 0
            pending_id = uuid4()
            with factory.begin() as session:
                session.add(
                    JobRow(
                        id=pending_id,
                        workspace_id=workspace_id,
                        episode_id=episode_id,
                        kind="INVESTIGATE",
                        status="QUEUED",
                        idempotency_key=uuid4().hex,
                        input_hash="b" * 64,
                        payload={"request": {"snapshot_id": str(first.snapshot_id)}},
                        attempts=0,
                        cancel_requested=False,
                    )
                )
            queued_plan = retire_snapshot(
                factory,
                driver,
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
            )
            assert queued_plan.pending_jobs == 1 and not queued_plan.eligible
            with pytest.raises(GraphReadError, match="pending jobs"):
                retire_snapshot(
                    factory,
                    driver,
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    apply=True,
                )
            with factory.begin() as session:
                pending = session.get(JobRow, pending_id)
                assert pending is not None
                pending.status = "CANCELLED"
            with reader_lease(factory, workspace_id, episode_id, first.snapshot_id):
                with pytest.raises(GraphReadError, match="leases"):
                    retire_snapshot(
                        factory,
                        driver,
                        workspace_id=workspace_id,
                        episode_id=episode_id,
                        snapshot_id=first.snapshot_id,
                        apply=True,
                    )
            with pytest.raises(GraphReadError, match="active"):
                retire_snapshot(
                    factory,
                    driver,
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=rebuilt.snapshot_id,
                    apply=True,
                )
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (i:Incident {snapshot_id:$sid}) WITH i LIMIT 1 DETACH DELETE i",
                    sid=str(first.snapshot_id),
                ).consume()
                graph.run(
                    "MATCH (a:Service)-[r:CALLS {snapshot_id:$sid}]->(b:Service) "
                    "WHERE a.service_key=$caller AND b.service_key=$callee "
                    "SET r.support_observation_ids=['malformed', $foreign]",
                    sid=str(first.snapshot_id),
                    caller=edge.caller_service_key,
                    callee=edge.callee_service_key,
                    foreign=str(uuid4()),
                ).consume()
                graph.run(
                    "MATCH (a:Service {snapshot_id:$sid,service_key:$caller}) "
                    "MATCH (b:Service {snapshot_id:$sid,service_key:$callee}) "
                    "CREATE (a)-[:CALLS {workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid,support_observation_ids:[$support]}]->(a) "
                    "CREATE (b)-[:CALLS {workspace_id:$wid,episode_id:$eid,"
                    "snapshot_id:$sid,support_observation_ids:[$support]}]->(a)",
                    sid=str(first.snapshot_id),
                    wid=str(workspace_id),
                    eid=str(episode_id),
                    caller=edge.caller_service_key,
                    callee=edge.callee_service_key,
                    support=str(edge.support_observation_ids[0]),
                ).consume()
            assert (
                str(first.snapshot_id)
                in graph_inventory(factory, driver)["ready_missing_or_invalid_projection_ids"]
            )
            assert not any(
                item.caller_service_key == edge.caller_service_key
                and item.callee_service_key == edge.callee_service_key
                for item in reader.eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    as_of_ns=cutoff,
                )
            )
            assert not reader.support_paths(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                as_of_ns=cutoff,
                source_service_key=edge.callee_service_key,
                target_service_key=edge.caller_service_key,
            )
            retired = retire_snapshot(
                factory,
                driver,
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=first.snapshot_id,
                apply=True,
            )
            assert not retired.dry_run and retired.graph_node_count == 0
            with pytest.raises(GraphReadError):
                reader.eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=first.snapshot_id,
                    as_of_ns=cutoff,
                )
            with factory() as session:
                sample = session.scalar(
                    select(ObservationRow).where(
                        ObservationRow.episode_id == episode_id,
                        ObservationRow.observation_type == "dependency",
                        ObservationRow.sample_count > 0,
                    )
                )
                assert sample is not None
                refs = session.scalars(
                    select(ObservationSpanRow).where(ObservationSpanRow.observation_id == sample.id)
                ).all()
                for ref in refs:
                    session.delete(ref)
                session.flush()
                with pytest.raises(GraphBuildError, match="lacks span support"):
                    load_graph_material(session, workspace_id, episode_id)
                session.rollback()
        finally:
            with driver.session(database="neo4j") as graph:
                for snapshot_id in snapshot_ids:
                    graph.run(
                        "MATCH (n {snapshot_id:$sid}) DETACH DELETE n",
                        sid=str(snapshot_id),
                    ).consume()


def test_graph_environment_identity_isolated(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, episode_dir
) -> None:
    directory, episode_id = episode_dir
    source = directory / "traces.jsonl"
    changed: list[str] = []
    for line in source.read_text().splitlines():
        item = json.loads(line)
        for resource in item["resourceSpans"]:
            for attribute in resource["resource"]["attributes"]:
                if attribute["key"] == "deployment.environment.name":
                    attribute["value"]["stringValue"] = "staging"
        changed.append(json.dumps(item, separators=(",", ":")))
    source_bytes = ("\n".join(changed) + "\n").encode()
    source.write_bytes(source_bytes)
    manifest_file = directory / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["sources"][0]["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    manifest["sources"][0]["bytes"] = len(source_bytes)
    manifest_file.write_text(json.dumps(manifest))
    submitted = submit_import(factory, artifacts, directory, idempotency_key="staging")
    assert ImportWorker(factory, artifacts, "staging-worker").run_once() == submitted.job_id
    workspace_id = UUID(manifest["workspace_id"])
    start = (
        int(
            datetime.fromisoformat(
                manifest["baseline_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    cutoff = (
        int(
            datetime.fromisoformat(
                manifest["event_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    materialize_signals(
        factory,
        artifacts,
        workspace_id=workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        built = build_snapshot(factory, driver, workspace_id=workspace_id, episode_id=episode_id)
        try:
            with factory() as session:
                keys = set(
                    session.scalars(
                        select(SpanRow.service_key).where(
                            SpanRow.episode_id == episode_id,
                        )
                    ).all()
                )
            assert keys
            with driver.session(database="neo4j") as graph:
                services = graph.run(
                    "MATCH (s:Service {snapshot_id:$sid}) "
                    "RETURN s.service_key AS service_key,s.environment AS environment,"
                    "s.namespace AS namespace,s.name AS name",
                    sid=str(built.snapshot_id),
                ).data()
            assert {row["service_key"] for row in services} == keys
            assert {row["environment"] for row in services} == {"staging"}
            assert all(
                row["service_key"]
                != AliasRegistry().resolve(workspace_id, row["namespace"], "demo", row["name"]).key
                for row in services
            )
            with pytest.raises(GraphReadError):
                GraphReader(factory, driver).eligible_calls(
                    workspace_id=workspace_id,
                    episode_id=uuid4(),
                    snapshot_id=built.snapshot_id,
                    as_of_ns=cutoff,
                )
        finally:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (n {snapshot_id:$sid}) DETACH DELETE n",
                    sid=str(built.snapshot_id),
                ).consume()


def test_document_snapshots_preserve_publication_cutoff_and_quotes(
    factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    episode_dir,
    test_database_name: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    directory, episode_id = episode_dir
    manifest = json.loads((directory / "manifest.json").read_text())
    workspace_id = UUID(manifest["workspace_id"])
    corpus = ROOT / "data/runbooks/demo"
    assert (
        cli_main(
            [
                "documents",
                "ingest",
                "--db-name",
                test_database_name,
                "--db-user",
                "postgres",
                "--password-file",
                str(ROOT / ".secrets/postgres_admin_password"),
                "--corpus-dir",
                str(corpus),
                "--artifact-root",
                str(artifacts.root),
            ]
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["documents"] == 24 and imported["chunks"] == 96
    assert ingest_corpus(factory, artifacts, corpus).reused_documents == 24
    changed_corpus = tmp_path / "changed-corpus"
    shutil.copytree(corpus, changed_corpus)
    changed_file = changed_corpus / "demo-doc-01.md"
    changed_bytes = changed_file.read_bytes().replace(b"local rendering", b"local formatting")
    changed_file.write_bytes(changed_bytes)
    changed_manifest = json.loads((changed_corpus / "manifest.json").read_text())
    changed_manifest["documents"][0]["sha256"] = hashlib.sha256(changed_bytes).hexdigest()
    changed_manifest["documents"][0]["bytes"] = len(changed_bytes)
    (changed_corpus / "manifest.json").write_text(json.dumps(changed_manifest))
    with pytest.raises(StorageConflict, match="source/version"):
        ingest_corpus(factory, artifacts, changed_corpus)
    submission = submit_import(factory, artifacts, directory, idempotency_key="document-graph")
    assert ImportWorker(factory, artifacts, "document-worker").run_once() == submission.job_id
    start = (
        int(
            datetime.fromisoformat(
                manifest["baseline_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    cutoff = (
        int(
            datetime.fromisoformat(
                manifest["event_interval"]["end"].replace("Z", "+00:00")
            ).timestamp()
        )
        * 1_000_000_000
    )
    materialize_signals(
        factory,
        artifacts,
        workspace_id=workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    later = int(datetime(2026, 2, 2, tzinfo=UTC).timestamp()) * 1_000_000_000
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        snapshot_ids: list[UUID] = []
        try:
            early = build_snapshot(
                factory,
                driver,
                workspace_id=workspace_id,
                episode_id=episode_id,
                artifacts=artifacts,
            )
            snapshot_ids.append(early.snapshot_id)
            assert early.documents == 18 and early.chunks == 72 and early.assertions == 36
            with driver.session(database="neo4j") as graph:
                retrievable = graph.run(
                    "MATCH (n:Retrievable {snapshot_id:$sid}) RETURN count(n) AS total",
                    sid=str(early.snapshot_id),
                ).single(strict=True)["total"]
            assert retrievable == early.observations + early.chunks
            assert (
                cli_main(
                    [
                        "documents",
                        "review",
                        "--db-name",
                        test_database_name,
                        "--db-user",
                        "postgres",
                        "--password-file",
                        str(ROOT / ".secrets/postgres_admin_password"),
                        "--episode-id",
                        str(episode_id),
                        "--artifact-root",
                        str(artifacts.root),
                    ]
                )
                == 0
            )
            review = json.loads(capsys.readouterr().out)
            assert review["documents"] == 18 and len(review["reviews"]) == 18
            reader = GraphReader(factory, driver)
            early_chunks = reader.eligible_document_chunks(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=early.snapshot_id,
                as_of_ns=cutoff,
                artifacts=artifacts,
            )
            assert len(early_chunks) == 72
            assert all(item["source_id"] <= "demo-doc-18" for item in early_chunks)
            request = RetrievalRequest(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=early.snapshot_id,
                as_of_ns=cutoff,
                mode="event",
                question="Why is checkout payment timing out?",
                top_k=12,
            )
            observed_corpus = corpus_from_reader(reader, artifacts, request)
            assert sum(item.kind == "chunk" for item in observed_corpus) == 72
            assert any(item.kind == "observation" for item in observed_corpus)
            embedder = MiniLMEmbedder()
            for strategy in ("lexical", "dense", "hybrid", "graph"):
                result = retrieve(
                    reader,
                    artifacts,
                    request,
                    strategy=strategy,
                    embedder=embedder,
                    embedding_cache_root=tmp_path / "embedding-cache",
                )
                assert result.candidates
                assert result.trace["observation_count"] > 0
                assert result.trace["document_chunk_count"] == 72
                assert all(item.snapshot_id == early.snapshot_id for item in result.candidates)
                assert all(item.as_of_ns == cutoff for item in result.candidates)
            assert (
                cli_main(
                    [
                        "retrieval",
                        "compare",
                        "--db-name",
                        test_database_name,
                        "--db-user",
                        "postgres",
                        "--password-file",
                        str(ROOT / ".secrets/postgres_admin_password"),
                        "--neo4j-auth-file",
                        str(ROOT / ".secrets/neo4j_auth"),
                        "--artifact-root",
                        str(artifacts.root),
                        "--embedding-cache-root",
                        str(tmp_path / "embedding-cache"),
                        "--episode-id",
                        str(episode_id),
                        "--snapshot-id",
                        str(early.snapshot_id),
                        "--as-of-ns",
                        str(cutoff),
                        "--question",
                        "Why is checkout payment timing out?",
                    ]
                )
                == 0
            )
            comparison = json.loads(capsys.readouterr().out)
            assert set(comparison) == {"lexical", "dense", "hybrid", "graph"}
            assert comparison["dense"]["trace"]["embedding_version"] is not None
            with pytest.raises(GraphReadError, match="availability"):
                reader.eligible_document_chunks(
                    workspace_id=workspace_id,
                    episode_id=episode_id,
                    snapshot_id=early.snapshot_id,
                    as_of_ns=cutoff,
                    artifacts=artifacts,
                    mode="available",
                )
            rebuilt = build_snapshot(
                factory,
                driver,
                workspace_id=workspace_id,
                episode_id=episode_id,
                artifacts=artifacts,
                document_cutoff_ns=later,
            )
            snapshot_ids.append(rebuilt.snapshot_id)
            assert rebuilt.documents == 24 and rebuilt.chunks == 96 and rebuilt.assertions == 48
            assert (
                len(
                    reader.eligible_document_chunks(
                        workspace_id=workspace_id,
                        episode_id=episode_id,
                        snapshot_id=rebuilt.snapshot_id,
                        as_of_ns=cutoff,
                        artifacts=artifacts,
                    )
                )
                == 72
            )
            early_cutoff_on_late_snapshot = RetrievalRequest(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=rebuilt.snapshot_id,
                as_of_ns=cutoff,
                mode="event",
                question="payment timeout",
            )
            scoped = corpus_from_reader(reader, artifacts, early_cutoff_on_late_snapshot)
            assert sum(item.kind == "chunk" for item in scoped) == 72
            assert all(
                item.source_locator.get("source_id", "demo-doc-01") <= "demo-doc-18"
                for item in scoped
            )
            with pytest.raises(GraphReadError, match="unavailable for a reader lease"):
                corpus_from_reader(
                    reader,
                    artifacts,
                    RetrievalRequest(
                        workspace_id=uuid4(),
                        episode_id=episode_id,
                        snapshot_id=rebuilt.snapshot_id,
                        as_of_ns=cutoff,
                        mode="event",
                        question="payment timeout",
                    ),
                )
            late_chunks = reader.eligible_document_chunks(
                workspace_id=workspace_id,
                episode_id=episode_id,
                snapshot_id=rebuilt.snapshot_id,
                as_of_ns=later,
                artifacts=artifacts,
            )
            assert len(late_chunks) == 96
            assert (
                len(
                    reader.eligible_document_chunks(
                        workspace_id=workspace_id,
                        episode_id=episode_id,
                        snapshot_id=early.snapshot_id,
                        as_of_ns=later,
                        artifacts=artifacts,
                    )
                )
                == 72
            )
            with driver.session(database="neo4j") as graph:
                assertions = graph.run(
                    "MATCH (a:Assertion {snapshot_id:$sid}) "
                    "RETURN a.document_id AS document_id,a.quote AS quote,"
                    "a.start_char AS start_char,a.end_char AS end_char,"
                    "a.predicate AS predicate",
                    sid=str(rebuilt.snapshot_id),
                ).data()
                invalid = graph.run(
                    "MATCH ()-[r:CAUSES {snapshot_id:$sid}]->() RETURN count(r) AS total",
                    sid=str(rebuilt.snapshot_id),
                ).single(strict=True)["total"]
            assert len(assertions) == 48 and invalid == 0
            with factory() as session:
                for assertion in assertions:
                    document = session.get(DocumentRow, UUID(assertion["document_id"]))
                    assert document is not None
                    source = artifacts.get_bytes(document.source_artifact_hash).decode()
                    assert (
                        source[assertion["start_char"] : assertion["end_char"]]
                        == assertion["quote"]
                    )
                    assert assertion["predicate"] in {
                        "DESCRIBES_SYMPTOM",
                        "SUGGESTS_CHECK",
                        "REPORTS_HISTORICAL_CAUSE",
                    }
        finally:
            with driver.session(database="neo4j") as graph:
                for snapshot_id in snapshot_ids:
                    graph.run(
                        "MATCH (n {snapshot_id:$sid}) DETACH DELETE n",
                        sid=str(snapshot_id),
                    ).consume()
            with factory.begin() as session:
                document_ids = session.scalars(
                    select(DocumentRow.id).where(
                        DocumentRow.workspace_id == workspace_id,
                        DocumentRow.source_id.like("demo-doc-%"),
                    )
                ).all()
                session.execute(delete(ChunkRow).where(ChunkRow.document_id.in_(document_ids)))
                session.execute(delete(DocumentRow).where(DocumentRow.id.in_(document_ids)))


def test_investigation_cited_reports_replay_and_tamper_rejection(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, tmp_path: Path
) -> None:
    source = ROOT / "data/generated/seed-20261010/03c9ef66-2609-5aec-86fa-9dda41e1c9af"
    directory = tmp_path / "investigation-episode"
    shutil.copytree(source, directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    episode_id = uuid4()
    manifest["episode_id"] = str(episode_id)
    manifest_path.write_text(json.dumps(manifest))
    first = submit_import(factory, artifacts, directory, idempotency_key="investigation")
    assert ImportWorker(factory, artifacts, "investigation-worker").run_once() == first.job_id
    assert (
        submit_import(factory, artifacts, directory, idempotency_key="investigation").job_id
        == first.job_id
    )
    start = int(
        datetime.fromisoformat(
            manifest["baseline_interval"]["end"].replace("Z", "+00:00")
        ).timestamp()
        * 1_000_000_000
    )
    late = int(
        datetime.fromisoformat(manifest["event_interval"]["end"].replace("Z", "+00:00")).timestamp()
        * 1_000_000_000
    )
    early = start + 60_000_000_000
    for cutoff in (early, late):
        materialize_signals(
            factory,
            artifacts,
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            investigation_start_ns=start,
            as_of_ns=cutoff,
        )
    snapshot_ids: list[UUID] = []
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        try:
            no_docs = build_snapshot(
                factory,
                driver,
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode_id,
                artifacts=artifacts,
            )
            snapshot_ids.append(no_docs.snapshot_id)
            service = InvestigationService(factory, driver, artifacts, tmp_path / "embedding-cache")

            def request(cutoff: int, symptom: str | None = None, snapshot_id: UUID | None = None):
                return InvestigationRequest(
                    episode_id=episode_id,
                    snapshot_id=snapshot_id,
                    as_of=ns_datetime(cutoff),
                    temporal_mode=TemporalMode.EVENT,
                    question="Which service should be checked first?",
                    symptom_service_id=symptom,
                    retrieval_mode=RetrievalMode.GRAPH,
                    ranking_mode=RankingMode.HEURISTIC,
                    generation_mode=GenerationMode.TEMPLATE,
                )

            no_document_report = service.investigate(
                DEFAULT_WORKSPACE_ID, request(early, snapshot_id=no_docs.snapshot_id)
            )
            assert all(item.kind == "observation" for item in no_document_report.evidence)
            assert not no_document_report.paths
            missing_model = service.investigate(
                DEFAULT_WORKSPACE_ID,
                request(early, snapshot_id=no_docs.snapshot_id).model_copy(
                    update={"ranking_mode": RankingMode.LEARNED}
                ),
            )
            assert missing_model.ranking_mode is RankingMode.HEURISTIC
            assert "learned_ranker_model_missing" in missing_model.warnings
            ingest_corpus(factory, artifacts, ROOT / "data/runbooks/demo")
            snapshot = build_snapshot(
                factory,
                driver,
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode_id,
                artifacts=artifacts,
            )
            snapshot_ids.append(snapshot.snapshot_id)
            reader = GraphReader(factory, driver)
            reports = [
                service.investigate(
                    DEFAULT_WORKSPACE_ID, request(cutoff, snapshot_id=snapshot.snapshot_id)
                )
                for cutoff in (early, late)
            ]
            assert all(report.narrative.status == "supported_hypotheses" for report in reports)
            assert reports[0].investigation_id != reports[1].investigation_id
            assert reports[0].evidence != reports[1].evidence
            assert all(
                item.interval is None or item.interval.end <= report.as_of
                for report in reports
                for item in report.evidence
            )
            for report in reports:
                measured = compute_persisted_signals(
                    factory,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    episode_id=episode_id,
                    as_of_ns=int(report.as_of.timestamp()) * 1_000_000_000,
                )
                anomalous = {item.service_key for item in measured.signals if is_anomalous(item)}
                assert {item.service_id for item in report.ranked_scores} <= anomalous
                assert all(
                    len(item.supporting_evidence_ids) == 2 for item in report.narrative.hypotheses
                )
                protected = retire_snapshot(
                    factory,
                    driver,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    episode_id=episode_id,
                    snapshot_id=snapshot.snapshot_id,
                )
                assert protected.saved_investigations >= 1 and not protected.eligible
            for report in reports:
                assert service.show(DEFAULT_WORKSPACE_ID, report.investigation_id).is_replayed
                for item in report.evidence:
                    opened = service.evidence(
                        DEFAULT_WORKSPACE_ID, report.investigation_id, item.evidence_id
                    )
                    assert opened["evidence"]["text"] == item.text
            assert service.investigate(
                DEFAULT_WORKSPACE_ID, request(late, snapshot_id=snapshot.snapshot_id)
            ).is_replayed
            calls = reader.eligible_calls(
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode_id,
                snapshot_id=snapshot.snapshot_id,
                as_of_ns=late,
                mode="event",
            )
            symptom = next(
                item.caller_service_key
                for item in calls
                if any(
                    item.callee_service_key == score.service_id
                    for score in reports[1].ranked_scores
                )
            )
            linked = service.investigate(
                DEFAULT_WORKSPACE_ID, request(late, symptom, snapshot.snapshot_id)
            )
            assert linked.paths
            assert all(edge.supporting_evidence_ids for path in linked.paths for edge in path.edges)
            for item in linked.evidence:
                service.evidence(DEFAULT_WORKSPACE_ID, linked.investigation_id, item.evidence_id)
            local_settings = RuntimeSettings(
                public_read_only=False,
                generation_provider=GenerationMode.OLLAMA,
                enable_local_model=True,
                operator_token=SecretStr("test-only"),
                ollama_model_digest=(
                    "sha256:0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
                ),
                ollama_url="http://127.0.0.1:11434",
            )

            class EchoProvider:
                def generate(self, bundle, template):
                    return GenerationOutput(template, 100, 50, 12.0, 1)

            model_request = request(late, symptom, snapshot.snapshot_id).model_copy(
                update={"generation_mode": GenerationMode.OLLAMA}
            )
            model_service = InvestigationService(
                factory,
                driver,
                artifacts,
                tmp_path / "embedding-cache",
                generation_provider=EchoProvider(),
                generation_settings=local_settings,
                generation_prompt_hash="a" * 64,
            )
            generated = model_service.investigate(DEFAULT_WORKSPACE_ID, model_request)
            assert generated.generation_origin.value == "ollama"
            assert generated.usage.prompt_tokens == 100 and generated.usage.output_tokens == 50
            assert model_service.investigate(DEFAULT_WORKSPACE_ID, model_request).is_replayed

            class InventedProvider:
                def generate(self, bundle, template):
                    raw = template.model_dump(mode="json")
                    raw["summary"]["evidence_ids"] = ["observation:invented"]
                    return GenerationOutput(Narrative.model_validate(raw), 100, 50, 12.0, 1)

            fallback_service = InvestigationService(
                factory,
                driver,
                artifacts,
                tmp_path / "embedding-cache",
                generation_provider=InventedProvider(),
                generation_settings=local_settings,
                generation_prompt_hash="b" * 64,
            )
            fallback = fallback_service.investigate(DEFAULT_WORKSPACE_ID, model_request)
            assert fallback.generation_origin.value == "template"
            assert fallback.fallback_reason == "ollama_support_invalid"
            assert fallback.narrative == generated.narrative
            unknown = service.investigate(
                DEFAULT_WORKSPACE_ID, request(late, "f" * 64, snapshot.snapshot_id)
            )
            assert unknown.narrative.status == "insufficient_evidence"
            assert "unknown_symptom_service" in unknown.warnings
            missing_baseline = service.investigate(
                DEFAULT_WORKSPACE_ID, request(start, snapshot_id=snapshot.snapshot_id)
            )
            assert missing_baseline.narrative.status == "insufficient_evidence"
            signal_report = compute_persisted_signals(
                factory,
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode_id,
                as_of_ns=late,
                mode="event",
            )
            keys, _ = generate_candidates(signal_report.signals, calls, symptom)
            scores = score_candidates(signal_report.signals, calls, keys, symptom)
            # Independent byte seek into the hash-named artifact, not a repeat
            # of the production resolver's metadata comparison.
            for citation in linked.evidence:
                if citation.kind == "observation":
                    payload = artifacts.get_bytes(citation.locator.source_hash)
                    offset = citation.locator.record_offset
                    assert offset is not None and (offset == 0 or payload[offset - 1] == 10)
                    span = json.loads(payload[offset:].split(b"\n", 1)[0])
                    assert span["trace_id"] == citation.locator.trace_id
                    assert span["span_id"] == citation.locator.span_ids[0]
                    assert span["episode_id"] == str(linked.episode_id)
                    assert span["workspace_id"] == str(linked.workspace_id)
                    assert int(span["end_ns"]) <= late
            for bad_field, bad_value in (("record_offset", 1), ("span_ids", ["f" * 16])):
                altered = linked.model_dump(mode="json")
                target = next(item for item in altered["evidence"] if item["metrics"])
                target["locator"][bad_field] = bad_value
                with pytest.raises(InvestigationValidationError, match="locator"):
                    validate_result(
                        InvestigationResult.model_validate(altered), reader, artifacts, scores
                    )
            altered = linked.model_dump(mode="json")
            altered["evidence"][0]["text"] += " invented"
            with pytest.raises(InvestigationValidationError, match="citation"):
                validate_result(
                    InvestigationResult.model_validate(altered), reader, artifacts, scores
                )
            altered = linked.model_dump(mode="json")
            target = next(item for item in altered["evidence"] if item["metrics"])
            target["metrics"][0]["value"] += 1
            with pytest.raises(InvestigationValidationError, match="numeric metric"):
                validate_result(
                    InvestigationResult.model_validate(altered), reader, artifacts, scores
                )
            altered = linked.model_dump(mode="json")
            altered["ranked_scores"][0]["score"] += 0.01
            with pytest.raises(InvestigationValidationError, match="rank or score"):
                validate_result(
                    InvestigationResult.model_validate(altered), reader, artifacts, scores
                )
            altered = linked.model_dump(mode="json")
            service_observation_id = altered["narrative"]["hypotheses"][0][
                "supporting_evidence_ids"
            ][0]
            altered["paths"][0]["edges"][0]["supporting_evidence_ids"] = [service_observation_id]
            with pytest.raises(
                InvestigationValidationError, match="relationship claim|path direction"
            ):
                validate_result(
                    InvestigationResult.model_validate(altered), reader, artifacts, scores
                )
            with factory.begin() as session:
                session.get(SnapshotRow, snapshot.snapshot_id).status = "BUILDING"
            with pytest.raises(StorageConflict, match="unpublished"):
                service.investigate(
                    DEFAULT_WORKSPACE_ID,
                    request(late - 1_000_000_000, snapshot_id=snapshot.snapshot_id),
                )
            with factory.begin() as session:
                session.get(SnapshotRow, snapshot.snapshot_id).status = "READY"
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (n:Snapshot {id:$sid}) DETACH DELETE n",
                    sid=str(snapshot.snapshot_id),
                ).consume()
            with pytest.raises((GraphReadError, StorageConflict)):
                service.investigate(
                    DEFAULT_WORKSPACE_ID,
                    request(late - 1_000_000_000, snapshot_id=snapshot.snapshot_id),
                )
        finally:
            with driver.session(database="neo4j") as graph:
                for snapshot_id in snapshot_ids:
                    graph.run(
                        "MATCH (n {snapshot_id:$sid}) DETACH DELETE n", sid=str(snapshot_id)
                    ).consume()


def test_investigation_healthy_public_fixture(
    factory: sessionmaker[Session], artifacts: ArtifactRepository, tmp_path: Path
) -> None:
    source = ROOT / "data/generated/seed-20261010/0afa58e4-aec3-563d-bba1-6fb5fb689a46"
    directory = tmp_path / "healthy-episode"
    shutil.copytree(source, directory)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    episode_id = uuid4()
    manifest["episode_id"] = str(episode_id)
    manifest_path.write_text(json.dumps(manifest))
    submission = submit_import(
        factory, artifacts, directory, idempotency_key="healthy-investigation"
    )
    assert ImportWorker(factory, artifacts, "healthy-worker").run_once() == submission.job_id
    start = int(
        datetime.fromisoformat(
            manifest["baseline_interval"]["end"].replace("Z", "+00:00")
        ).timestamp()
        * 1_000_000_000
    )
    cutoff = int(
        datetime.fromisoformat(manifest["event_interval"]["end"].replace("Z", "+00:00")).timestamp()
        * 1_000_000_000
    )
    materialize_signals(
        factory,
        artifacts,
        workspace_id=DEFAULT_WORKSPACE_ID,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    quiet_cutoffs = [
        point
        for point in range(start + 10_000_000_000, cutoff + 1, 10_000_000_000)
        if not any(
            is_anomalous(feature)
            for feature in compute_persisted_signals(
                factory,
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode_id,
                as_of_ns=point,
            ).signals
        )
    ]
    assert quiet_cutoffs
    quiet = quiet_cutoffs[-1]
    ingest_corpus(factory, artifacts, ROOT / "data/runbooks/demo")
    with driver_from_auth_file("bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth") as driver:
        snapshot = build_snapshot(
            factory,
            driver,
            workspace_id=DEFAULT_WORKSPACE_ID,
            episode_id=episode_id,
            artifacts=artifacts,
        )
        try:
            result = InvestigationService(
                factory, driver, artifacts, tmp_path / "healthy-embedding-cache"
            ).investigate(
                DEFAULT_WORKSPACE_ID,
                InvestigationRequest(
                    episode_id=episode_id,
                    snapshot_id=snapshot.snapshot_id,
                    as_of=ns_datetime(quiet),
                    temporal_mode=TemporalMode.EVENT,
                    question="Is there an anomaly?",
                    symptom_service_id=None,
                    retrieval_mode=RetrievalMode.GRAPH,
                    ranking_mode=RankingMode.HEURISTIC,
                    generation_mode=GenerationMode.TEMPLATE,
                ),
            )
            assert result.narrative.status == "no_anomaly_detected"
            assert not result.ranked_scores and not result.narrative.hypotheses
        finally:
            with driver.session(database="neo4j") as graph:
                graph.run(
                    "MATCH (n {snapshot_id:$sid}) DETACH DELETE n", sid=str(snapshot.snapshot_id)
                ).consume()


def test_nullable_span_fields_preserve_bulk_insert_batches(factory, artifacts, episode_dir):
    import math

    from sqlalchemy import event

    from tracecause.jobs.imports import BATCH_SIZE

    directory, episode_id = episode_dir
    submitted = submit_import(factory, artifacts, directory, idempotency_key="nullable-batch")
    with factory() as session:
        engine = session.bind
    insert_calls = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO spans "):
            insert_calls.append(1)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert (
            ImportWorker(factory, artifacts, "nullable-batch-worker").run_once() == submitted.job_id
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    with factory() as session:
        rows = session.scalars(select(SpanRow).where(SpanRow.episode_id == episode_id)).all()
    assert any(row.parent_span_id is None for row in rows)
    assert any(row.parent_span_id is not None for row in rows)
    assert len(insert_calls) == math.ceil(len(rows) / BATCH_SIZE)
