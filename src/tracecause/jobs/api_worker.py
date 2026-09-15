"""Durable API pipeline reusing the existing domain services and publication checks."""

import argparse
import json
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import structlog
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tracecause.api.resources import Resources, open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import EpisodeManifest, GenerationMode, InvestigationRequest
from tracecause.documents.persistence import ingest_documents
from tracecause.graph.projection import build_snapshot
from tracecause.ingestion.source_allowlist import resolve_episode_sources
from tracecause.investigation.generation import OllamaProvider
from tracecause.investigation.service import InvestigationService
from tracecause.investigation.validation import as_ns
from tracecause.jobs.api_jobs import API_KINDS
from tracecause.jobs.imports import ImportWorker, submit_import
from tracecause.jobs.queue import JobConflict, JobQueue
from tracecause.operational.replay import materialize_replay
from tracecause.operational.storage import ingest_operations
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import AuditEventRow, EpisodeRow, JobRow
from tracecause.telemetry.persistence import materialize_signals


class JobCancelled(ValueError):
    """Cancellation or lost lease prevents any result publication."""


class ApiWorker:
    def __init__(self, resources: Resources, worker_id: str | None = None):
        self.resources = resources
        self.worker_id = worker_id or f"api-worker-{uuid4()}"
        self.queue = JobQueue(resources.factory)
        self._lease_lost = threading.Event()
        self._stage_started = time.perf_counter()
        self._stage = "claimed"

    def guard(self, session: Session, job_id: UUID, result_id: UUID | None = None) -> JobRow:
        row = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
        if (
            row is None
            or row.status != "RUNNING"
            or row.lease_owner != self.worker_id
            or row.cancel_requested
            or row.lease_until is None
            or row.lease_until <= datetime.now(UTC)
            or self._lease_lost.is_set()
        ):
            raise JobCancelled("job_cancelled_or_lease_lost")
        if result_id is not None:
            row.payload = {**row.payload, "result_id": str(result_id)}
            row.status, row.checkpoint = "SUCCEEDED", "published"
            row.lease_owner = row.lease_until = None
            row.updated_at = datetime.now(UTC)
            row.finished_at = row.updated_at
            session.add(
                AuditEventRow(
                    id=uuid4(),
                    request_id=row.payload.get("request_id", str(job_id)),
                    actor_type="worker",
                    action="job_publish",
                    resource_id=str(result_id),
                    outcome="succeeded",
                )
            )
        return row

    def checkpoint(self, job_id: UUID, phase: str) -> None:
        with self.resources.factory.begin() as session:
            row = self.guard(session, job_id)
            structlog.get_logger().info(
                "job_stage",
                job_id=str(job_id),
                request_id=row.payload.get("request_id"),
                episode_id=str(row.episode_id),
                stage=self._stage,
                duration_ms=round((time.perf_counter() - self._stage_started) * 1000, 3),
                next_stage=phase,
            )
        self._stage, self._stage_started = phase, time.perf_counter()
        self.queue.checkpoint(job_id, self.worker_id, phase)

    def process(self, job: JobRow) -> None:
        resources = self.resources

        def publish(session: Session, result_id: UUID) -> None:
            self.guard(session, job.id, result_id)

        if job.kind == "INVESTIGATE":
            request = InvestigationRequest.model_validate(job.payload["request"])
            self.checkpoint(job.id, "investigating")
            provider = None
            if request.generation_mode is GenerationMode.OLLAMA:
                provider = OllamaProvider(
                    resources.settings,
                    prompt_root=Path.cwd(),
                    slot_path=resources.artifacts.root / "generation.lock",
                )
            try:
                service = InvestigationService(
                    resources.factory,
                    resources.driver,
                    resources.artifacts,
                    resources.artifacts.root / "embedding-cache",
                    generation_provider=provider,
                    generation_settings=resources.settings,
                    generation_prompt_hash=provider.prompt_hash if provider else None,
                )
                service.investigate(DEFAULT_WORKSPACE_ID, request, publication_guard=publish)
            finally:
                if provider is not None:
                    provider.client.close()
            return
        directory = resources.staging / str(UUID(job.payload["bundle_id"]))
        manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
        if manifest.workspace_id != job.workspace_id or manifest.episode_id != job.episode_id:
            raise ValueError("staged_scope_changed")
        sources = resolve_episode_sources(directory, manifest)
        self.checkpoint(job.id, "normalizing")
        imported = submit_import(
            resources.factory,
            resources.artifacts,
            directory,
            idempotency_key=f"api-stage:{job.id}",
            job_kind="IMPORT_STAGE",
        )
        with resources.factory() as session:
            stage = session.get(JobRow, imported.job_id)
            assert stage is not None
            stage_status = stage.status
        if stage_status != "SUCCEEDED":
            claimed = self.queue.claim(
                self.worker_id, kinds=("IMPORT_STAGE",), job_id=imported.job_id
            )
            if claimed is None:
                raise JobConflict("import stage unavailable")
            ImportWorker(resources.factory, resources.artifacts, self.worker_id).process_claimed(
                claimed.id
            )
        ingest_operations(resources.factory, resources.artifacts, manifest, sources)
        self.checkpoint(job.id, "documents")
        documents = tuple(
            (path, source.sha256)
            for source, path in zip(manifest.sources, sources, strict=True)
            if source.kind == "markdown"
        )
        if documents:
            ingest_documents(resources.factory, resources.artifacts, documents)
        with resources.factory() as session:
            episode = session.get(EpisodeRow, job.episode_id)
            if episode is None or episode.analysis_start_ns is None:
                raise ValueError("baseline_required")
            start, end = episode.analysis_start_ns, as_ns(episode.event_end)
        self.checkpoint(job.id, "signals")
        if episode.data_quality.get("operational_application_id"):
            materialize_replay(
                resources.factory,
                resources.artifacts,
                job.workspace_id,
                episode.id,
                checkpoint=lambda: self.checkpoint(job.id, "signals"),
            )
        else:
            materialize_signals(
                resources.factory,
                resources.artifacts,
                workspace_id=job.workspace_id,
                episode_id=episode.id,
                investigation_start_ns=start,
                as_of_ns=end,
                include_history_buckets=True,
            )
        self.checkpoint(job.id, "graph")
        build_snapshot(
            resources.factory,
            resources.driver,
            workspace_id=job.workspace_id,
            episode_id=episode.id,
            artifacts=resources.artifacts,
            publication_guard=publish,
            snapshot_id=uuid5(job.id, "api-import-snapshot"),
        )

    def run_once(self, job_id: UUID | None = None) -> UUID | None:
        job = self.queue.claim(self.worker_id, kinds=API_KINDS, job_id=job_id)
        if job is None:
            return None
        self._stage, self._stage_started = "claimed", time.perf_counter()
        started = time.perf_counter()
        stop = threading.Event()
        self._lease_lost.clear()

        def heartbeat() -> None:
            while not stop.wait(5):
                try:
                    if not self.queue.heartbeat(job.id, self.worker_id):
                        self._lease_lost.set()
                        return
                except SQLAlchemyError:
                    self._lease_lost.set()
                    structlog.get_logger().warning("worker_heartbeat_failed", job_id=str(job.id))
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            self.process(job)
        except JobCancelled:
            with self.resources.factory.begin() as session:
                row = session.scalar(select(JobRow).where(JobRow.id == job.id).with_for_update())
                if (
                    row is not None
                    and row.status == "RUNNING"
                    and row.lease_owner == self.worker_id
                ):
                    row.status = "CANCELLED" if row.cancel_requested else "RETRY_WAIT"
                    row.next_retry_at = datetime.now(UTC)
                    row.lease_owner = row.lease_until = None
                    row.updated_at = datetime.now(UTC)
                    row.finished_at = row.updated_at if row.status == "CANCELLED" else None
        except (SQLAlchemyError, Neo4jError, DriverError, OSError, JobConflict) as exc:
            structlog.get_logger().warning(
                "job_dependency_failed", job_id=str(job.id), error_type=type(exc).__name__
            )
            self.queue.fail(job.id, self.worker_id, "dependency_unavailable", transient=True)
        except (ValueError, KeyError):
            self.queue.fail(job.id, self.worker_id, "job_validation_failed", transient=False)
        finally:
            stop.set()
            thread.join(timeout=6)
            structlog.get_logger().info(
                "job_attempt_finished",
                job_id=str(job.id),
                request_id=job.payload.get("request_id"),
                episode_id=str(job.episode_id),
                stage=self._stage,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        return job.id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    resources = open_resources(load_settings(Path("configs/defaults.yaml")))
    worker = ApiWorker(resources)
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat = Path("/tmp/tracecause-worker-heartbeat")
    try:
        while not stopping:
            heartbeat.touch()
            identifier = worker.run_once()
            if identifier is not None:
                print(json.dumps({"processed_job_id": str(identifier)}), flush=True)
            if args.once:
                return
            if identifier is None:
                time.sleep(0.5)
    finally:
        resources.close()


if __name__ == "__main__":
    main()
