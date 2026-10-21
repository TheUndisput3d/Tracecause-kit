"""Versioned HTTP boundary: scoped reads and authenticated durable submissions."""

import hmac
import os
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecause.api import reads
from tracecause.api.errors import ApiError, envelope
from tracecause.api.json_limits import JsonDepthLimit
from tracecause.api.metrics import ApiMetrics
from tracecause.api.resources import Resources, open_resources
from tracecause.api.uploads import BUNDLE_LIMIT, FILE_LIMIT, stage_upload
from tracecause.config.settings import RuntimeSettings, load_settings
from tracecause.contracts.canonical import semantic_sha256
from tracecause.contracts.models import (
    ApiCapabilities,
    AuthStatus,
    DataOrigin,
    EpisodePage,
    EpisodeView,
    ErrorEnvelope,
    EvaluationPage,
    EvaluationReport,
    Evidence,
    GenerationMode,
    GraphResponse,
    HealthStatus,
    ImportSubmissionView,
    InvestigationPage,
    InvestigationRequest,
    InvestigationSubmission,
    InvestigationView,
    JobStatus,
    JobView,
    KnowledgeChecksResponse,
    KnowledgeResponse,
    RankingMode,
    SeriesResponse,
    TemporalMode,
)
from tracecause.graph.errors import GraphReadError
from tracecause.investigation.generation import GenerationError, OllamaProvider
from tracecause.jobs.api_jobs import ApiJobs, scoped_episode
from tracecause.jobs.queue import JobConflict, JobQueue
from tracecause.operational import reads as operational_reads
from tracecause.operational.api_models import OperationsResponse
from tracecause.operational.handoffs import HandoffEvidence
from tracecause.operational.models import OperationalEvidence
from tracecause.storage.artifacts import ArtifactError
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, StorageConflict
from tracecause.storage.schema import EpisodeRow, SnapshotRow


class Boundaries:
    def __init__(self, app: ASGIApp, settings: RuntimeSettings, metrics: ApiMetrics | None = None):
        self.app, self.settings = app, settings
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id
        started, status, size = time.perf_counter(), 500, 0
        limit = BUNDLE_LIMIT if scope["path"] == "/api/v1/imports" else 64 * 1024

        content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
        json_depth = (
            JsonDepthLimit()
            if not content_type
            or content_type.split(b";")[0].lower() == b"application/json"
            or content_type.split(b";")[0].lower().endswith(b"+json")
            else None
        )

        async def bounded_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    raise ApiError(413, "upload_too_large", "The request exceeds the byte limit.")
                if json_depth is not None:
                    json_depth.feed(message.get("body", b""))
            return message

        async def tagged_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", str(request_id).encode()),
                ]
            await send(message)

        try:
            if self.settings.public_read_only and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                raise ApiError(403, "public_read_only", "This instance serves saved results only.")
            raw_length = dict(scope.get("headers", [])).get(b"content-length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ApiError(400, "invalid_length", "The content length is invalid.") from exc
                if length < 0 or length > limit:
                    raise ApiError(413, "upload_too_large", "The request exceeds the byte limit.")
            await self.app(scope, bounded_receive, tagged_send)
        except ApiError as exc:
            await JSONResponse(envelope(exc, request_id), status_code=exc.status)(
                scope, receive, tagged_send
            )
        finally:
            if self.metrics is not None:
                self.metrics.observe(
                    scope["method"],
                    getattr(scope.get("route"), "path", "unmatched"),
                    status,
                    time.perf_counter() - started,
                )
            structlog.get_logger().info(
                "http_request",
                request_id=str(request_id),
                method=scope["method"],
                route=getattr(scope.get("route"), "path", "unmatched"),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )


def create_app(
    settings: RuntimeSettings | None = None, resources: Resources | None = None
) -> FastAPI:
    settings = settings or load_settings(
        Path(os.environ.get("TRACECAUSE_CONFIG", "configs/defaults.yaml"))
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.JSONRenderer(),
            ]
        )
        owned = resources is None
        application.state.resources = resources
        if owned:
            try:
                application.state.resources = open_resources(settings)
            except ValueError:
                structlog.get_logger().warning("api_dependencies_unconfigured")
        try:
            yield
        finally:
            if owned and application.state.resources is not None:
                application.state.resources.close()

    application = FastAPI(
        title="TraceCause",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            code: {"model": ErrorEnvelope}
            for code in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)
        },
    )
    metrics = ApiMetrics()
    application.add_middleware(Boundaries, settings=settings, metrics=metrics)

    def dependencies(request: Request) -> Resources:
        value: Resources | None = getattr(request.app.state, "resources", None)
        if value is None:
            raise ApiError(
                503, "dependencies_unavailable", "Required storage is unavailable.", retryable=True
            )
        return value

    def operator(authorization: Annotated[str | None, Header()] = None) -> None:
        if settings.public_read_only:
            raise ApiError(403, "public_read_only", "This instance serves saved results only.")
        token = settings.operator_token
        provided = (
            authorization.removeprefix("Bearer ")
            if authorization and authorization.startswith("Bearer ")
            else ""
        )
        if token is None or not hmac.compare_digest(
            provided.encode(), token.get_secret_value().encode()
        ):
            raise ApiError(401, "unauthorized", "An operator bearer token is required.")

    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(envelope(exc, request.state.request_id), status_code=exc.status)

    application.add_exception_handler(ApiError, api_error)  # type: ignore[arg-type]

    @application.exception_handler(RequestValidationError)
    @application.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        return await api_error(
            request, ApiError(422, "invalid_request", "The request did not match the contract.")
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return await api_error(
            request,
            ApiError(exc.status_code, "invalid_request", "The request could not be handled."),
        )

    @application.exception_handler(SQLAlchemyError)
    @application.exception_handler(Neo4jError)
    @application.exception_handler(DriverError)
    @application.exception_handler(ArtifactError)
    async def dependency_error(request: Request, exc: Exception) -> JSONResponse:
        return await api_error(
            request,
            ApiError(
                503, "dependencies_unavailable", "Required storage is unavailable.", retryable=True
            ),
        )

    @application.exception_handler(StorageConflict)
    @application.exception_handler(JobConflict)
    @application.exception_handler(GraphReadError)
    async def conflict_error(request: Request, exc: Exception) -> JSONResponse:
        return await api_error(
            request,
            ApiError(409, "resource_conflict", "The resource state conflicts with this request."),
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        structlog.get_logger().error(
            "api_internal_error",
            request_id=str(request.state.request_id),
            exception_type=type(exc).__name__,
        )
        return await api_error(
            request, ApiError(500, "internal_error", "The request could not complete.")
        )

    @application.get("/internal/metrics", include_in_schema=False, dependencies=[Depends(operator)])
    def internal_metrics(r: Annotated[Resources, Depends(dependencies)]) -> Response:
        return Response(metrics.render(r.factory), media_type="text/plain; version=0.0.4")

    @application.get(
        "/health/live", response_model=HealthStatus, response_model_exclude_defaults=True
    )
    def live() -> HealthStatus:
        return HealthStatus(status="alive")

    @application.get("/health/ready", response_model=HealthStatus)
    def ready(r: Annotated[Resources, Depends(dependencies)]) -> HealthStatus:
        with r.factory() as session:
            session.execute(text("SELECT 1"))
            invalid = session.scalar(
                select(EpisodeRow.id)
                .outerjoin(SnapshotRow, SnapshotRow.id == EpisodeRow.active_snapshot_id)
                .where(
                    EpisodeRow.workspace_id == DEFAULT_WORKSPACE_ID,
                    EpisodeRow.active_snapshot_id.is_not(None),
                    (SnapshotRow.id.is_(None)) | (SnapshotRow.status != "READY"),
                )
                .limit(1)
            )
            if invalid:
                raise ApiError(
                    503,
                    "snapshot_unavailable",
                    "An active snapshot is unavailable.",
                    retryable=True,
                )
        r.driver.verify_connectivity()
        with r.factory() as session:
            active = session.scalar(
                select(EpisodeRow)
                .where(
                    EpisodeRow.workspace_id == DEFAULT_WORKSPACE_ID,
                    EpisodeRow.active_snapshot_id.is_not(None),
                )
                .order_by(EpisodeRow.created_at.desc())
                .limit(1)
            )
            if active is not None and active.active_snapshot_id is not None:
                from tracecause.investigation.validation import as_ns

                try:
                    r.reader._validate_snapshot(
                        session,
                        DEFAULT_WORKSPACE_ID,
                        active.id,
                        active.active_snapshot_id,
                        as_ns(active.event_end),
                        "event",
                    )
                except GraphReadError as exc:
                    raise ApiError(
                        503,
                        "snapshot_unavailable",
                        "An active snapshot is unavailable.",
                        retryable=True,
                    ) from exc
        degraded = False
        if settings.enable_local_model:
            provider = None
            try:
                provider = OllamaProvider(
                    settings,
                    prompt_root=Path.cwd(),
                    slot_path=r.artifacts.root / "generation.lock",
                )
                degraded = not provider.capability()["digest_match"]
            except GenerationError:
                degraded = True
            finally:
                if provider is not None:
                    provider.client.close()
        return HealthStatus(status="ready", generation_degraded=degraded)

    @application.get("/api/v1/config", response_model=ApiCapabilities)
    def capabilities(request: Request) -> ApiCapabilities:
        modes = [GenerationMode.TEMPLATE]
        if (
            settings.enable_local_model
            and not settings.public_read_only
            and settings.generation_provider.value == "ollama"
        ):
            modes.append(GenerationMode.OLLAMA)
        rankings = [RankingMode.HEURISTIC]
        r = getattr(request.app.state, "resources", None)
        if r is not None:
            try:
                ranker, _ = r.investigations._promoted_ranker(DEFAULT_WORKSPACE_ID)
                if ranker is not None:
                    rankings.append(RankingMode.LEARNED)
            except (SQLAlchemyError, ArtifactError):
                structlog.get_logger().warning("ranker_capability_unavailable")
        return ApiCapabilities(
            config_hash=semantic_sha256(
                {
                    "generation_modes": [item.value for item in modes],
                    "ranking_modes": [item.value for item in rankings],
                    "embedding_revision": settings.embedding_revision,
                    "graph_nodes": settings.graph_max_nodes,
                    "graph_edges": settings.graph_max_edges,
                    "graph_hops": settings.graph_max_hops,
                    "question_characters": settings.question_characters,
                    "model_digest": settings.ollama_model_digest,
                }
            ),
            ranking_modes=rankings,
            public_read_only=settings.public_read_only,
            mutations_enabled=not settings.public_read_only,
            generation_modes=modes,
            maximum_question_characters=settings.question_characters,
            maximum_graph_hops=settings.graph_max_hops,
            maximum_upload_file_bytes=FILE_LIMIT,
            maximum_upload_bundle_bytes=BUNDLE_LIMIT,
            limitations=[
                "Controlled demonstration data; no production validation.",
                "Evaluation labels and claims received AI review, not independent human review.",
                "Learned ranking is experimental; captured transfer did not establish superiority.",
            ],
        )

    @application.get(
        "/api/v1/auth/check", response_model=AuthStatus, dependencies=[Depends(operator)]
    )
    def auth_check() -> AuthStatus:
        return AuthStatus(authorized=True, mutations_enabled=True)

    @application.get("/api/v1/episodes", response_model=EpisodePage)
    def list_episodes(
        r: Annotated[Resources, Depends(dependencies)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        origin: DataOrigin | None = None,
    ) -> EpisodePage:
        return reads.episodes(r, limit, cursor, origin)

    @application.get("/api/v1/episodes/{episode_id}", response_model=EpisodeView)
    def get_episode(
        episode_id: UUID, r: Annotated[Resources, Depends(dependencies)]
    ) -> EpisodeView:
        with r.factory() as session:
            return reads.episode_view(session, scoped_episode(session, episode_id))

    @application.get("/api/v1/episodes/{episode_id}/series", response_model=SeriesResponse)
    def get_series(
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> SeriesResponse:
        return reads.series(r, episode_id, snapshot_id, as_of, temporal_mode)

    @application.get("/api/v1/graph", response_model=GraphResponse)
    def get_graph(
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
        focus_service_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
        max_hops: Annotated[int, Query(ge=1, le=3)] = 3,
    ) -> GraphResponse:
        return reads.graph(
            r, episode_id, snapshot_id, as_of, temporal_mode, focus_service_id, max_hops
        )

    @application.get("/api/v1/operations", response_model=OperationsResponse)
    def get_operations(
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
        symptom_service_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
    ) -> OperationsResponse:
        return operational_reads.operations(
            r, episode_id, snapshot_id, as_of, temporal_mode, symptom_service_id
        )

    @application.get(
        "/api/v1/operations/evidence/{evidence_id}", response_model=OperationalEvidence
    )
    def get_operational_evidence(
        evidence_id: str,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> OperationalEvidence:
        return operational_reads.evidence(
            r, evidence_id, episode_id, snapshot_id, as_of, temporal_mode
        )

    @application.get("/api/v1/operations/handoffs/{evidence_id}", response_model=HandoffEvidence)
    def get_handoff_evidence(
        evidence_id: str,
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> HandoffEvidence:
        return operational_reads.handoff_source(
            r, evidence_id, episode_id, snapshot_id, as_of, temporal_mode
        )

    @application.get("/api/v1/knowledge", response_model=KnowledgeResponse)
    def get_knowledge(
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> KnowledgeResponse:
        return reads.knowledge(r, episode_id, snapshot_id, as_of, temporal_mode)

    @application.get("/api/v1/knowledge/checks", response_model=KnowledgeChecksResponse)
    def get_knowledge_checks(
        episode_id: UUID,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        symptom_service_id: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> KnowledgeChecksResponse:
        return reads.knowledge_checks(
            r, episode_id, snapshot_id, as_of, temporal_mode, symptom_service_id
        )

    @application.get("/api/v1/evidence/{evidence_id}", response_model=Evidence)
    def get_evidence(
        evidence_id: str,
        snapshot_id: UUID,
        as_of: AwareDatetime,
        r: Annotated[Resources, Depends(dependencies)],
        temporal_mode: TemporalMode = TemporalMode.EVENT,
    ) -> Evidence:
        return reads.evidence(r, evidence_id, snapshot_id, as_of, temporal_mode)

    @application.get("/api/v1/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: UUID, r: Annotated[Resources, Depends(dependencies)]) -> JobView:
        with r.factory() as session:
            return reads.job_view(reads.scoped_job(session, job_id))

    @application.post(
        "/api/v1/jobs/{job_id}/cancel", response_model=JobView, dependencies=[Depends(operator)]
    )
    def cancel_job(job_id: UUID, r: Annotated[Resources, Depends(dependencies)]) -> JobView:
        with r.factory() as session:
            reads.scoped_job(session, job_id)
        JobQueue(r.factory).cancel(job_id)
        return get_job(job_id, r)

    @application.post(
        "/api/v1/investigations",
        response_model=InvestigationSubmission,
        status_code=202,
        dependencies=[Depends(operator)],
    )
    def submit_investigation(
        body: InvestigationRequest,
        request: Request,
        r: Annotated[Resources, Depends(dependencies)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    ) -> InvestigationSubmission:
        job = ApiJobs(r.factory, settings).submit_investigation(
            body, idempotency_key, request.state.request_id
        )
        return InvestigationSubmission(
            investigation_id=job.id, job_id=job.id, status=JobStatus(job.status)
        )

    @application.get("/api/v1/investigations/{investigation_id}", response_model=InvestigationView)
    def get_investigation(
        investigation_id: UUID, r: Annotated[Resources, Depends(dependencies)]
    ) -> InvestigationView:
        return reads.investigation(r, investigation_id)

    @application.get(
        "/api/v1/investigations/{investigation_id}/export", response_model=InvestigationView
    )
    def export_investigation(
        investigation_id: UUID, r: Annotated[Resources, Depends(dependencies)]
    ) -> JSONResponse:
        view = reads.investigation(r, investigation_id)
        if view.result is None:
            raise ApiError(409, "result_not_ready", "The investigation result is not ready.")
        return JSONResponse(
            view.model_dump(mode="json"),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="investigation-{investigation_id}.json"'
                )
            },
        )

    @application.get(
        "/api/v1/episodes/{episode_id}/investigations", response_model=InvestigationPage
    )
    def list_investigations(
        episode_id: UUID,
        r: Annotated[Resources, Depends(dependencies)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ) -> InvestigationPage:
        return reads.investigations(r, episode_id, limit, cursor)

    @application.get("/api/v1/evaluations", response_model=EvaluationPage)
    def list_evaluations(
        r: Annotated[Resources, Depends(dependencies)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ) -> EvaluationPage:
        return reads.evaluations(r, limit, cursor)

    @application.get("/api/v1/evaluations/{report_id}", response_model=EvaluationReport)
    def get_evaluation(
        report_id: UUID, r: Annotated[Resources, Depends(dependencies)]
    ) -> EvaluationReport:
        return reads.evaluation(r, report_id)

    @application.post(
        "/api/v1/imports",
        response_model=ImportSubmissionView,
        status_code=202,
        dependencies=[Depends(operator)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["manifest", "files"],
                            "properties": {
                                "manifest": {
                                    "type": "string",
                                    "description": "Serialized EpisodeManifest JSON",
                                },
                                "files": {
                                    "type": "array",
                                    "items": {"type": "string", "format": "binary"},
                                    "maxItems": 100,
                                },
                            },
                        }
                    }
                },
            }
        },
    )
    async def submit_upload(
        request: Request,
        r: Annotated[Resources, Depends(dependencies)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    ) -> ImportSubmissionView:
        directory = await stage_upload(request, r.staging)
        try:
            job = await run_in_threadpool(
                ApiJobs(r.factory, settings).submit_import,
                directory,
                idempotency_key,
                request.state.request_id,
            )
            if job.payload["bundle_id"] != directory.name:
                shutil.rmtree(directory)
        except BaseException:
            shutil.rmtree(directory)
            raise
        return ImportSubmissionView(
            episode_id=UUID(job.payload["manifest"]["episode_id"]),
            job_id=job.id,
            status=JobStatus(job.status),
        )

    return application


app = create_app()
