"""PostgreSQL lease queue with scoped idempotency and explicit transitions."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from tracecause.storage.schema import EpisodeRow, JobRow

MAX_ATTEMPTS = 3
LEASE_SECONDS = 30


class JobConflict(ValueError):
    """A job key, lease or state conflicts with the requested transition."""


class JobQueue:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue_import(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        episode_id: UUID,
        idempotency_key: str,
        input_hash: str,
        fingerprint: str,
        payload: dict[str, object],
        kind: str = "IMPORT",
    ) -> JobRow:
        if not idempotency_key or len(idempotency_key) > 200:
            raise JobConflict("invalid idempotency key")
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        job_id = uuid4()
        statement = (
            insert(JobRow)
            .values(
                id=job_id,
                workspace_id=workspace_id,
                episode_id=episode_id,
                kind=kind,
                status="QUEUED",
                idempotency_key=key_hash,
                input_hash=input_hash,
                import_fingerprint=fingerprint,
                payload=payload,
                attempts=0,
                checkpoint="staged",
                cancel_requested=False,
            )
            .on_conflict_do_nothing()
            .returning(JobRow.id)
        )
        inserted_id = session.execute(statement).scalar_one_or_none()
        if inserted_id is not None:
            session.flush()
            row = session.get(JobRow, inserted_id)
            assert row is not None
            return row
        by_key = session.scalar(
            select(JobRow).where(
                JobRow.workspace_id == workspace_id,
                JobRow.episode_id == episode_id,
                JobRow.idempotency_key == key_hash,
            )
        )
        if by_key is not None:
            if by_key.input_hash != input_hash:
                raise JobConflict("idempotency key used for different input")
            return by_key
        by_fingerprint = session.scalar(
            select(JobRow).where(JobRow.import_fingerprint == fingerprint)
        )
        if by_fingerprint is not None and by_fingerprint.input_hash == input_hash:
            return by_fingerprint
        raise JobConflict("import fingerprint conflicts with existing input")

    def claim(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        kinds: tuple[str, ...] = ("IMPORT",),
        job_id: UUID | None = None,
    ) -> JobRow | None:
        if not worker_id or len(worker_id) > 100:
            raise JobConflict("invalid worker identity")
        instant = now or datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(JobRow)
                .where(
                    JobRow.kind.in_(kinds),
                    JobRow.id == job_id if job_id is not None else true(),
                    or_(
                        JobRow.status == "QUEUED",
                        and_(JobRow.status == "RETRY_WAIT", JobRow.next_retry_at <= instant),
                        and_(JobRow.status == "RUNNING", JobRow.lease_until <= instant),
                    ),
                )
                .order_by(JobRow.created_at, JobRow.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.cancel_requested:
                row.status = "CANCELLED"
                row.lease_owner = None
                row.lease_until = None
                row.updated_at = instant
                row.finished_at = instant
                self._set_episode_status(session, row, "CANCELLED")
                return None
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "FAILED"
                row.safe_error = "lease_attempts_exhausted"
                row.lease_owner = None
                row.lease_until = None
                row.updated_at = instant
                row.finished_at = instant
                self._set_episode_status(session, row, "FAILED")
                return None
            row.status = "RUNNING"
            row.started_at = row.started_at or instant
            row.attempts += 1
            row.lease_owner = worker_id
            row.lease_until = instant + timedelta(seconds=LEASE_SECONDS)
            row.heartbeat_at = instant
            row.next_retry_at = None
            row.updated_at = instant
            session.flush()
            return row

    @staticmethod
    def _set_episode_status(session: Session, job: JobRow, status: str) -> None:
        if job.kind not in {"IMPORT", "IMPORT_STAGE", "API_IMPORT"} or job.episode_id is None:
            return
        episode = session.get(EpisodeRow, job.episode_id)
        if episode is not None and episode.ingestion_status != "IMPORTED":
            episode.ingestion_status = status

    def heartbeat(self, job_id: UUID, worker_id: str, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(UTC)
        with self.session_factory.begin() as session:
            result = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.status == "RUNNING",
                    JobRow.lease_owner == worker_id,
                )
                .values(
                    heartbeat_at=instant,
                    lease_until=instant + timedelta(seconds=LEASE_SECONDS),
                    updated_at=instant,
                )
            )
            changed = cast(CursorResult[Any], result).rowcount
            return changed == 1

    def checkpoint(self, job_id: UUID, worker_id: str, stage: str) -> None:
        if not stage or len(stage) > 40:
            raise JobConflict("invalid checkpoint")
        with self.session_factory.begin() as session:
            result = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.status == "RUNNING",
                    JobRow.lease_owner == worker_id,
                )
                .values(checkpoint=stage, updated_at=datetime.now(UTC))
            )
            changed = cast(CursorResult[Any], result).rowcount
            if changed != 1:
                raise JobConflict("checkpoint lease is not owned")

    def cancel(self, job_id: UUID) -> str:
        with self.session_factory.begin() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            if row is None:
                raise JobConflict("job not found")
            if row.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return row.status
            row.cancel_requested = True
            if row.status in {"QUEUED", "RETRY_WAIT"}:
                row.status = "CANCELLED"
                row.finished_at = datetime.now(UTC)
                self._set_episode_status(session, row, "CANCELLED")
            row.updated_at = datetime.now(UTC)
            return row.status

    def fail(self, job_id: UUID, worker_id: str, code: str, *, transient: bool) -> str:
        if not code or len(code) > 100 or not code.replace("_", "").isalnum():
            raise JobConflict("unsafe failure code")
        instant = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            if row is None or row.status != "RUNNING" or row.lease_owner != worker_id:
                raise JobConflict("failure lease is not owned")
            row.safe_error = code
            row.lease_owner = None
            row.lease_until = None
            row.updated_at = instant
            if row.cancel_requested:
                row.status = "CANCELLED"
            elif transient and row.attempts < MAX_ATTEMPTS:
                row.status = "RETRY_WAIT"
                row.next_retry_at = instant + timedelta(seconds=min(2**row.attempts, 60))
            else:
                row.status = "FAILED"
            if row.status in {"FAILED", "CANCELLED"}:
                row.finished_at = instant
            if row.status in {"FAILED", "CANCELLED"} and row.kind in {
                "IMPORT",
                "IMPORT_STAGE",
                "API_IMPORT",
            }:
                self._set_episode_status(session, row, row.status)
            return row.status

    def retry(self, job_id: UUID) -> str:
        with self.session_factory.begin() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            if row is None or row.status != "FAILED":
                raise JobConflict("only failed jobs can be retried")
            row.status = "QUEUED"
            row.attempts = 0
            row.started_at = row.finished_at = None
            row.lease_owner = None
            row.lease_until = None
            row.next_retry_at = None
            row.cancel_requested = False
            row.safe_error = None
            row.checkpoint = "staged"
            row.updated_at = datetime.now(UTC)
            self._set_episode_status(session, row, "QUEUED")
            return row.status
