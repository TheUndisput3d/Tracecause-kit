"""Scoped API submissions; admission and idempotency are one short SQL transaction."""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tracecause.api.errors import ApiError
from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.canonical import semantic_sha256
from tracecause.contracts.models import (
    EpisodeManifest,
    GenerationMode,
    InvestigationRequest,
    TemporalMode,
)
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, register_episode
from tracecause.storage.schema import (
    AuditEventRow,
    EpisodeRow,
    JobRow,
    SnapshotRow,
    SpanRow,
    WorkspaceRow,
)

API_KINDS = ("API_IMPORT", "INVESTIGATE")


def scoped_episode(session: Session, episode_id: UUID) -> EpisodeRow:
    row = session.get(EpisodeRow, episode_id)
    if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID:
        raise ApiError(404, "not_found", "The resource was not found.")
    return row


def scoped_snapshot(
    session: Session,
    episode_id: UUID,
    snapshot_id: UUID | None,
    as_of: datetime,
    mode: TemporalMode,
    *,
    lock: bool = False,
) -> tuple[EpisodeRow, SnapshotRow]:
    episode = scoped_episode(session, episode_id)
    chosen = snapshot_id or episode.active_snapshot_id
    query = select(SnapshotRow).where(SnapshotRow.id == chosen)
    row = session.scalar(query.with_for_update() if lock else query) if chosen else None
    if row is None or row.workspace_id != DEFAULT_WORKSPACE_ID or row.episode_id != episode_id:
        raise ApiError(404, "not_found", "The resource was not found.")
    if row.status != "READY" or episode.ingestion_status != "IMPORTED":
        raise ApiError(409, "snapshot_unavailable", "The requested snapshot is not ready.")
    if not episode.event_start <= as_of <= episode.event_end:
        raise ApiError(422, "invalid_cutoff", "The cutoff must fall within the episode.")
    if mode is TemporalMode.AVAILABLE:
        missing = (
            select(SpanRow.span_id)
            .where(SpanRow.episode_id == episode_id, SpanRow.available_at.is_(None))
            .exists()
        )
        if session.scalar(select(missing)):
            raise ApiError(
                422, "availability_unrecorded", "This episode supports event-time replay only."
            )
    return episode, row


class ApiJobs:
    def __init__(self, factory: sessionmaker[Session], settings: RuntimeSettings):
        self.factory, self.settings = factory, settings

    def submit(
        self,
        kind: str,
        key: str,
        payload: dict[str, Any],
        *,
        request: InvestigationRequest | None = None,
        manifest: EpisodeManifest | None = None,
        manifest_hash: str | None = None,
        request_id: UUID | None = None,
    ) -> JobRow:
        if kind not in API_KINDS or not re.fullmatch(r"[\x21-\x7e]{1,200}", key):
            raise ApiError(422, "invalid_idempotency_key", "A bounded Idempotency-Key is required.")
        key_hash = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()
        input_hash = semantic_sha256(
            request.model_dump(mode="json") if request else payload["manifest"]
        )
        with self.factory.begin() as session:
            session.execute(
                insert(WorkspaceRow)
                .values(id=DEFAULT_WORKSPACE_ID, name="default")
                .on_conflict_do_nothing()
            )
            session.scalar(
                select(WorkspaceRow)
                .where(WorkspaceRow.id == DEFAULT_WORKSPACE_ID)
                .with_for_update()
            )
            existing = session.scalar(
                select(JobRow).where(
                    JobRow.workspace_id == DEFAULT_WORKSPACE_ID,
                    JobRow.kind == kind,
                    JobRow.idempotency_key == key_hash,
                )
            )
            if existing:
                if existing.input_hash != input_hash:
                    raise ApiError(
                        409, "idempotency_conflict", "This key was used for another request."
                    )
                return existing
            active = session.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.workspace_id == DEFAULT_WORKSPACE_ID,
                    JobRow.kind.in_(API_KINDS),
                    JobRow.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT")),
                )
            )
            if active is not None and active >= 10:
                raise ApiError(429, "queue_full", "The operator queue is full.", retryable=True)
            if request is not None:
                if not request.question.strip():
                    raise ApiError(422, "empty_question", "A question is required.")
                if len(request.question) > self.settings.question_characters:
                    raise ApiError(
                        422, "question_too_long", "The question exceeds the configured limit."
                    )
                if request.generation_mode is GenerationMode.OLLAMA and not (
                    self.settings.enable_local_model
                    and self.settings.generation_provider is GenerationMode.OLLAMA
                ):
                    raise ApiError(403, "generation_disabled", "Local generation is not enabled.")
                episode, snapshot = scoped_snapshot(
                    session,
                    request.episode_id,
                    request.snapshot_id,
                    request.as_of,
                    request.temporal_mode,
                    lock=True,
                )
                if request.symptom_service_id is not None:
                    service = request.symptom_service_id
                    if not re.fullmatch(r"[0-9a-f]{64}", service) or not session.scalar(
                        select(SpanRow.span_id)
                        .where(
                            SpanRow.episode_id == episode.id,
                            SpanRow.service_key == service,
                        )
                        .limit(1)
                    ):
                        raise ApiError(
                            422, "invalid_symptom", "The symptom service is outside this episode."
                        )
                payload = {
                    "request": request.model_copy(update={"snapshot_id": snapshot.id}).model_dump(
                        mode="json"
                    )
                }
            else:
                if (
                    manifest is None
                    or manifest_hash is None
                    or manifest.workspace_id != DEFAULT_WORKSPACE_ID
                ):
                    raise ApiError(
                        422, "invalid_manifest", "The manifest is outside the configured scope."
                    )
                episode = register_episode(session, manifest, manifest_hash)
            request_id = request_id or uuid4()
            payload = {**payload, "request_id": str(request_id)}
            row = JobRow(
                id=uuid4(),
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=episode.id,
                kind=kind,
                status="QUEUED",
                idempotency_key=key_hash,
                input_hash=input_hash,
                payload=payload,
                attempts=0,
                checkpoint="queued",
                cancel_requested=False,
            )
            session.add(
                AuditEventRow(
                    id=uuid4(),
                    request_id=str(request_id),
                    actor_type="operator",
                    action="job_submit",
                    resource_id=str(row.id),
                    outcome="queued",
                )
            )
            session.add(row)
            session.flush()
            return row

    def submit_investigation(
        self, request: InvestigationRequest, key: str, request_id: UUID | None = None
    ) -> JobRow:
        return self.submit("INVESTIGATE", key, {}, request=request, request_id=request_id)

    def submit_import(self, directory: Path, key: str, request_id: UUID | None = None) -> JobRow:
        raw = (directory / "manifest.json").read_bytes()
        manifest = EpisodeManifest.model_validate_json(raw)
        return self.submit(
            "API_IMPORT",
            key,
            {"bundle_id": directory.name, "manifest": manifest.model_dump(mode="json")},
            manifest=manifest,
            manifest_hash=hashlib.sha256(raw).hexdigest(),
            request_id=request_id,
        )
