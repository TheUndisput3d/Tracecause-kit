"""Stage redacted imports and persist them with lease-owned transactions."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import insert, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tracecause.contracts.canonical import bytes_sha256
from tracecause.contracts.models import EpisodeManifest, ImportDiagnostic, NormalizedSpan
from tracecause.ingestion.normalize import (
    NormalizedBatch,
    normalize_otlp,
    normalized_span_semantic_hash,
    serialized_spans,
)
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ingestion.redaction import REDACTION_VERSION
from tracecause.ingestion.source_allowlist import resolve_episode_sources
from tracecause.jobs.queue import JobConflict, JobQueue
from tracecause.storage.artifacts import ArtifactError, ArtifactRepository
from tracecause.storage.postgres import StorageConflict, register_artifact, register_episode
from tracecause.storage.schema import EpisodeRow, JobRow, SourceArtifactRow, SpanRow

NORMALIZATION_VERSION = f"otlp-json-v1:redaction-{REDACTION_VERSION}:aliases-1.0"
BATCH_SIZE = 500


class ImportValidationError(ValueError):
    """The episode cannot be staged or persisted safely."""

    def __init__(
        self,
        code: str,
        *,
        diagnostics: tuple[ImportDiagnostic, ...] = (),
        rejected_count: int = 0,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics
        self.rejected_count = rejected_count


@dataclass(frozen=True)
class ImportSubmission:
    job_id: UUID
    episode_id: UUID
    status: str
    normalized_artifact_hash: str
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    diagnostics: tuple[ImportDiagnostic, ...]


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_sources(manifest: EpisodeManifest, paths: tuple[Path, ...]) -> NormalizedBatch:
    batches = []
    for source, path in zip(manifest.sources, paths, strict=True):
        if source.kind in {"markdown", "operational_json"}:
            continue
        encoding = (
            InputEncoding.OTLP_JSONL_V1
            if source.kind == "otlp_jsonl"
            else InputEncoding.OTLP_JSON_V1
        )
        with path.open("rb") as reader:
            batch = normalize_otlp(
                reader,
                encoding=encoding,
                workspace_id=manifest.workspace_id,
                episode_id=manifest.episode_id,
                expected_source_hash=source.sha256,
                source_name=source.logical_name,
            )
        if batch.fatal or batch.source_hash != source.sha256 or path.stat().st_size != source.bytes:
            raise ImportValidationError(
                "source_integrity_failed",
                diagnostics=batch.diagnostics,
                rejected_count=batch.rejected_count,
            )
        batches.append(batch)
    if not batches:
        raise ImportValidationError("telemetry_source_required")
    if len(batches) == 1:
        return batches[0]
    unique: dict[tuple[str, str], NormalizedSpan] = {}
    duplicates = sum(b.duplicate_count for b in batches)
    for batch in batches:
        for span in batch.spans:
            key = (span.trace_id, span.span_id)
            if key in unique:
                if normalized_span_semantic_hash(unique[key]) != normalized_span_semantic_hash(
                    span
                ):
                    raise ImportValidationError("conflicting_cross_source_span")
                duplicates += 1
            else:
                unique[key] = span
    spans = tuple(unique[key] for key in sorted(unique))
    return NormalizedBatch(
        spans=spans,
        accepted_count=len(spans),
        rejected_count=sum(b.rejected_count for b in batches),
        duplicate_count=duplicates,
        conflict_count=sum(b.conflict_count for b in batches),
        diagnostics=tuple(d for b in batches for d in b.diagnostics)[:100],
        source_hash=_hash_json([b.source_hash for b in batches]),
        redacted_hash=bytes_sha256(serialized_spans(spans)),
        fatal=False,
    )


def submit_import(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    episode_dir: Path,
    *,
    idempotency_key: str,
    allow_partial: bool = False,
    job_kind: str = "IMPORT",
) -> ImportSubmission:
    if "gold" in episode_dir.resolve().parts or episode_dir.is_symlink():
        raise ImportValidationError("unsafe_episode_directory")
    manifest_file = episode_dir / "manifest.json"
    if manifest_file.is_symlink():
        raise ImportValidationError("unsafe_manifest_file")
    manifest_bytes = manifest_file.read_bytes()
    manifest = EpisodeManifest.model_validate_json(manifest_bytes)
    sources = resolve_episode_sources(episode_dir, manifest)
    batch = normalize_sources(manifest, tuple(sources))
    if batch.rejected_count and not allow_partial:
        raise ImportValidationError(
            "source_records_rejected",
            diagnostics=batch.diagnostics,
            rejected_count=batch.rejected_count,
        )
    normalized = serialized_spans(batch.spans)
    descriptor = artifacts.put_bytes(normalized)
    if descriptor.content_hash != batch.redacted_hash:
        raise ImportValidationError("redacted_artifact_hash_mismatch")
    manifest_hash = bytes_sha256(manifest_bytes)
    fingerprint = _hash_json(
        [
            str(manifest.workspace_id),
            str(manifest.episode_id),
            batch.source_hash,
            NORMALIZATION_VERSION,
        ]
    )
    input_hash = _hash_json(
        [manifest_hash, batch.source_hash, descriptor.content_hash, NORMALIZATION_VERSION]
    )
    payload: dict[str, object] = {
        "source_hash": batch.source_hash,
        "normalized_artifact_hash": descriptor.content_hash,
        "normalization_version": NORMALIZATION_VERSION,
        "accepted_count": batch.accepted_count,
        "rejected_count": batch.rejected_count,
        "duplicate_count": batch.duplicate_count,
        "diagnostics": [item.model_dump(mode="json") for item in batch.diagnostics],
    }
    queue = JobQueue(session_factory)
    for attempt in range(2):
        try:
            with session_factory.begin() as session:
                register_episode(session, manifest, manifest_hash)
                register_artifact(
                    session,
                    manifest.workspace_id,
                    manifest.episode_id,
                    descriptor,
                    kind="normalized_otlp_jsonl",
                    redaction_version=REDACTION_VERSION,
                    purpose="import_source",
                )
                job = queue.enqueue_import(
                    session,
                    workspace_id=manifest.workspace_id,
                    episode_id=manifest.episode_id,
                    idempotency_key=idempotency_key,
                    input_hash=input_hash,
                    fingerprint=fingerprint,
                    payload=payload,
                    kind=job_kind,
                )
                result = ImportSubmission(
                    job.id,
                    manifest.episode_id,
                    job.status,
                    descriptor.content_hash,
                    batch.accepted_count,
                    batch.rejected_count,
                    batch.duplicate_count,
                    batch.diagnostics,
                )
            return result
        except IntegrityError:
            if attempt == 1:
                raise StorageConflict("concurrent import could not be reconciled") from None
    raise StorageConflict("concurrent import could not be reconciled")


def _insert_chunk(
    session: Session,
    episode_id: UUID,
    artifact_hash: str,
    spans: list[NormalizedSpan],
) -> int:
    keys = [(episode_id, span.trace_id, span.span_id) for span in spans]
    existing_rows = session.execute(
        select(SpanRow.trace_id, SpanRow.span_id, SpanRow.normalized_hash).where(
            tuple_(SpanRow.episode_id, SpanRow.trace_id, SpanRow.span_id).in_(keys)
        )
    ).all()
    existing = {(row.trace_id, row.span_id): row.normalized_hash for row in existing_rows}
    to_insert: list[dict[str, object]] = []
    for span in spans:
        digest = normalized_span_semantic_hash(span)
        previous = existing.get((span.trace_id, span.span_id))
        if previous is not None:
            if previous != digest:
                raise StorageConflict("conflicting duplicate span in persisted episode")
            continue
        to_insert.append(
            {
                "episode_id": episode_id,
                "workspace_id": span.workspace_id,
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "namespace": span.service_namespace,
                "environment": span.service_environment,
                "service_name": span.service_name,
                "service_key": span.service_key,
                "parent_span_id": span.parent_span_id,
                "operation": span.span_name,
                "start_ns": int(span.start_ns),
                "end_ns": int(span.end_ns),
                "kind": span.kind,
                "status": span.status,
                "safe_attributes": span.safe_attributes,
                "source_artifact_hash": artifact_hash,
                "source_record_offset": span.source_record_offset,
                "source_line_number": span.source_line_number,
                "source_resource_index": span.source_resource_index,
                "source_scope_index": span.source_scope_index,
                "source_span_index": span.source_span_index,
                "available_at": span.available_at,
                "quality_flags": span.quality_flags,
                "normalized_hash": digest,
            }
        )
    if to_insert:
        # Uniform nullable columns preserve one driver batch per500 spans.
        # Measured multi-VALUES compilation was slower on the reference host.
        session.execute(insert(SpanRow).execution_options(render_nulls=True), to_insert)
    return len(to_insert)


class ImportWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        artifacts: ArtifactRepository,
        worker_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.artifacts = artifacts
        self.worker_id = worker_id
        self.queue = JobQueue(session_factory)

    def process_claimed(self, job_id: UUID) -> int:
        with self.session_factory() as session:
            job = session.get(JobRow, job_id)
            if (
                job is None
                or job.kind not in {"IMPORT", "IMPORT_STAGE"}
                or job.lease_owner != self.worker_id
            ):
                raise JobConflict("import lease is not owned")
            payload = dict(job.payload)
        artifact_hash = payload.get("normalized_artifact_hash")
        if not isinstance(artifact_hash, str):
            raise ImportValidationError("missing_import_artifact")
        self.artifacts.verify(artifact_hash)
        if not self.queue.heartbeat(job_id, self.worker_id):
            raise JobConflict("import lease was cancelled or lost")
        self.queue.checkpoint(job_id, self.worker_id, "validated")
        inserted = 0
        with self.session_factory.begin() as session:
            job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            if job is None or job.status != "RUNNING" or job.lease_owner != self.worker_id:
                raise JobConflict("import lease was lost")
            if job.cancel_requested:
                job.status = "CANCELLED"
                job.lease_owner = None
                job.lease_until = None
                job.updated_at = datetime.now(UTC)
                job.finished_at = job.updated_at
                if job.episode_id is not None:
                    episode_for_cancel = session.get(EpisodeRow, job.episode_id)
                    if (
                        episode_for_cancel is not None
                        and episode_for_cancel.ingestion_status != "IMPORTED"
                    ):
                        episode_for_cancel.ingestion_status = "CANCELLED"
                return 0
            if job.lease_until is None or job.lease_until <= datetime.now(UTC):
                raise JobConflict("import lease expired before publication")
            if job.episode_id is None:
                raise ImportValidationError("missing_import_episode")
            episode = session.scalar(
                select(EpisodeRow).where(EpisodeRow.id == job.episode_id).with_for_update()
            )
            artifact = session.get(SourceArtifactRow, artifact_hash)
            if episode is None or artifact is None or artifact.workspace_id != job.workspace_id:
                raise ImportValidationError("import_scope_missing")
            pending: list[NormalizedSpan] = []
            parsed = 0
            for line in self.artifacts.iter_lines(artifact_hash):
                span = NormalizedSpan.model_validate_json(line)
                if span.episode_id != job.episode_id or span.workspace_id != job.workspace_id:
                    raise ImportValidationError("normalized_span_scope_mismatch")
                pending.append(span)
                parsed += 1
                if len(pending) == BATCH_SIZE:
                    inserted += _insert_chunk(session, job.episode_id, artifact_hash, pending)
                    pending.clear()
            if pending:
                inserted += _insert_chunk(session, job.episode_id, artifact_hash, pending)
            if parsed != payload.get("accepted_count"):
                raise ImportValidationError("normalized_span_count_mismatch")
            if job.cancel_requested:
                job.status = "CANCELLED"
                job.lease_owner = None
                job.lease_until = None
                job.updated_at = datetime.now(UTC)
                job.finished_at = job.updated_at
                if episode.ingestion_status != "IMPORTED":
                    episode.ingestion_status = "CANCELLED"
                return 0
            episode.ingestion_status = "IMPORTED"
            episode.data_quality = {
                "accepted_count": parsed,
                "rejected_count": payload.get("rejected_count"),
                "duplicate_count": payload.get("duplicate_count"),
                "original_source_hash": payload.get("source_hash"),
                "redacted_artifact_hash": artifact_hash,
                "normalization_version": payload.get("normalization_version"),
            }
            job.status = "SUCCEEDED"
            job.checkpoint = "spans_persisted"
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = datetime.now(UTC)
            job.finished_at = job.updated_at
        return inserted

    def run_once(self) -> UUID | None:
        claimed = self.queue.claim(self.worker_id)
        if claimed is None:
            return None
        try:
            self.process_claimed(claimed.id)
        except (ArtifactError, ValidationError, ImportValidationError, StorageConflict) as exc:
            code = (
                "artifact_invalid"
                if isinstance(exc, ArtifactError)
                else "normalized_span_invalid"
                if isinstance(exc, ValidationError)
                else "import_conflict"
                if isinstance(exc, StorageConflict)
                else exc.code
            )
            self.queue.fail(claimed.id, self.worker_id, code, transient=False)
        return claimed.id
