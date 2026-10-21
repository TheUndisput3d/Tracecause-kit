"""Strict public and internal data boundaries; no evaluation truth enters runtime models."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
EpochNanoseconds = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DataOrigin(StrEnum):
    SYNTHETIC = "synthetic"
    CAPTURED_DEMO = "captured_demo"
    EXTERNAL = "external"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SnapshotStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    TOMBSTONED = "TOMBSTONED"


class TemporalMode(StrEnum):
    EVENT = "event_time_replay"
    AVAILABLE = "available_time_replay"


class RetrievalMode(StrEnum):
    DENSE = "DENSE"
    LEXICAL = "LEXICAL"
    HYBRID = "HYBRID"
    GRAPH = "GRAPH"


class RankingMode(StrEnum):
    HEURISTIC = "heuristic"
    LEARNED = "learned"


class GenerationMode(StrEnum):
    TEMPLATE = "template"
    OLLAMA = "ollama"


class GenerationOrigin(StrEnum):
    TEMPLATE = "template"
    OLLAMA = "ollama"


class TimeInterval(Contract):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "TimeInterval":
        if self.end <= self.start:
            raise ValueError("interval end must be after start")
        return self


class SourceMetadata(Contract):
    logical_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$")]
    kind: Literal["otlp_json", "otlp_jsonl", "markdown", "operational_json"]
    sha256: Sha256
    bytes: int = Field(ge=0)
    published_at: AwareDatetime | None

    @field_validator("logical_name")
    @classmethod
    def safe_logical_name(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("logical source name cannot be a path component")
        return value


class EpisodeManifest(Contract):
    schema_version: Literal["1.0"]
    episode_id: UUID
    workspace_id: UUID
    display_name: str = Field(max_length=120)
    origin: Literal[DataOrigin.SYNTHETIC, DataOrigin.CAPTURED_DEMO]
    event_interval: TimeInterval
    baseline_interval: TimeInterval | None
    availability_recorded: bool
    sources: list[SourceMetadata] = Field(min_length=1, max_length=100)
    public_notes: str = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_baseline(self) -> "EpisodeManifest":
        if self.baseline_interval and (
            self.baseline_interval.start < self.event_interval.start
            or self.baseline_interval.end > self.event_interval.end
        ):
            raise ValueError("baseline must be contained in episode event interval")
        return self


class ImportRequest(Contract):
    manifest: EpisodeManifest
    dry_run: bool
    idempotency_key: NonEmpty


class ImportDiagnostic(Contract):
    code: NonEmpty
    source_name: NonEmpty | None
    record_offset: int | None = Field(ge=0)
    safe_message: str = Field(max_length=500)


class ImportReport(Contract):
    workspace_id: UUID
    episode_id: UUID
    source_manifest_hash: Sha256
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    diagnostics: list[ImportDiagnostic] = Field(max_length=20)


class ServiceIdentity(Contract):
    workspace_id: UUID
    namespace: str
    environment: str
    name: NonEmpty
    service_key: Sha256
    alias_version: NonEmpty


class NormalizedSpan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: UUID
    episode_id: UUID
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None
    service_key: NonEmpty | None
    service_namespace: str | None = None
    service_environment: str | None = None
    service_name: str | None = None
    alias_version: str = "1.0"
    alias_source_reference: str | None = None
    span_name: str = Field(max_length=500)
    kind: Literal["INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER", "UNSPECIFIED"]
    status: Literal["UNSET", "OK", "ERROR"]
    start_ns: EpochNanoseconds
    end_ns: EpochNanoseconds
    available_at: AwareDatetime | None
    safe_attributes: dict[str, str | int | float | bool]
    source_hash: Sha256
    source_record_offset: int = Field(ge=0)
    source_line_number: int = Field(default=1, ge=1)
    source_resource_index: int = Field(default=0, ge=0)
    source_scope_index: int = Field(default=0, ge=0)
    source_span_index: int = Field(default=0, ge=0)
    quality_flags: list[NonEmpty] = Field(max_length=30)

    @field_validator("trace_id", "span_id", "parent_span_id")
    @classmethod
    def reject_zero_id(cls, value: str | None) -> str | None:
        if value is not None and set(value) == {"0"}:
            raise ValueError("trace/span ID cannot be zero")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> "NormalizedSpan":
        if int(self.end_ns) > 2**63 - 1 or int(self.start_ns) > 2**63 - 1:
            raise ValueError("nanosecond timestamp exceeds BIGINT range")
        if int(self.end_ns) < int(self.start_ns):
            raise ValueError("span end precedes start")
        return self


class AggregateObservation(Contract):
    observation_id: UUID
    workspace_id: UUID
    episode_id: UUID
    rule_version: NonEmpty
    service_key: NonEmpty | None
    caller_service_key: NonEmpty | None
    callee_service_key: NonEmpty | None
    observation_type: Literal["service", "dependency"]
    start_ns: EpochNanoseconds
    end_ns: EpochNanoseconds
    sample_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    p50_ms: FiniteFloat | None
    p95_ms: FiniteFloat | None
    provenance_hash: Sha256
    available_at: AwareDatetime | None
    quality_flags: list[NonEmpty] = Field(max_length=30)

    @model_validator(mode="after")
    def validate_counts(self) -> "AggregateObservation":
        if int(self.end_ns) > 2**63 - 1 or int(self.start_ns) > 2**63 - 1:
            raise ValueError("nanosecond timestamp exceeds BIGINT range")
        if self.error_count > self.sample_count or int(self.end_ns) <= int(self.start_ns):
            raise ValueError("invalid observation count or interval")
        if self.observation_type == "service" and not self.service_key:
            raise ValueError("service observation requires service_key")
        if self.observation_type == "dependency" and (
            not self.caller_service_key or not self.callee_service_key
        ):
            raise ValueError("dependency observation requires caller and callee")
        return self


class Document(Contract):
    document_id: UUID
    workspace_id: UUID
    source_hash: Sha256
    version: NonEmpty
    title: NonEmpty
    published_at: AwareDatetime | None
    available_at: AwareDatetime | None
    document_type: Literal["runbook", "historical_note", "other"]


class Chunk(Contract):
    chunk_id: UUID
    document_id: UUID
    version: NonEmpty
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    token_count: int = Field(ge=1, le=220)
    normalized_text_hash: Sha256
    embedding_artifact_hash: Sha256 | None
    chunking_version: NonEmpty

    @model_validator(mode="after")
    def valid_offset(self) -> "Chunk":
        if self.end_char <= self.start_char:
            raise ValueError("chunk offsets must increase")
        return self


class Assertion(Contract):
    assertion_id: UUID
    chunk_id: UUID
    subject_service_id: NonEmpty | None
    predicate: Literal["DESCRIBES_SYMPTOM", "SUGGESTS_CHECK", "REPORTS_HISTORICAL_CAUSE"]
    object_text: NonEmpty
    quote_start_char: int = Field(ge=0)
    quote_end_char: int = Field(gt=0)
    extractor_version: NonEmpty
    verified: bool


class Snapshot(Contract):
    snapshot_id: UUID
    workspace_id: UUID
    episode_id: UUID
    graph_schema_version: NonEmpty
    input_manifest_hash: Sha256
    embedding_revision: NonEmpty
    status: SnapshotStatus
    created_at: AwareDatetime
    published_at: AwareDatetime | None
    error_code: NonEmpty | None

    @model_validator(mode="after")
    def ready_is_published(self) -> "Snapshot":
        if self.status is SnapshotStatus.READY and self.published_at is None:
            raise ValueError("READY snapshot needs published_at")
        return self


class PublicError(Contract):
    code: NonEmpty
    message: str = Field(max_length=500)
    retryable: bool


class PublicRequestError(PublicError):
    request_id: UUID


class ErrorEnvelope(Contract):
    error: PublicRequestError


class Job(Contract):
    job_id: UUID
    kind: Literal["import", "investigate", "build_snapshot", "evaluate"]
    status: JobStatus
    phase: NonEmpty | None
    completed_units: int = Field(ge=0)
    total_units: int | None = Field(ge=0)
    attempt_count: int = Field(ge=0)
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    cancel_requested: bool
    error: PublicError | None


class InvestigationRequest(Contract):
    episode_id: UUID
    snapshot_id: UUID | None = None
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    question: str = Field(min_length=1, max_length=2000)
    symptom_service_id: NonEmpty | None = None
    retrieval_mode: RetrievalMode
    ranking_mode: RankingMode
    generation_mode: GenerationMode


class EvidenceLocator(Contract):
    source_hash: Sha256
    record_offset: int | None = Field(ge=0)
    start_char: int | None = Field(ge=0)
    end_char: int | None = Field(ge=0)
    trace_id: TraceId | None
    span_ids: list[SpanId] = Field(max_length=20)

    @model_validator(mode="after")
    def valid_offsets(self) -> "EvidenceLocator":
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char <= self.start_char
        ):
            raise ValueError("source excerpt offsets must increase")
        return self


class Metric(Contract):
    observation_id: NonEmpty
    metric_name: Literal[
        "request_count",
        "error_count",
        "error_fraction",
        "p50_ms",
        "p95_ms",
        "latency_anomaly",
        "completeness",
    ]
    value: FiniteFloat
    unit: Literal["count", "fraction", "ms", "score"]
    interval: TimeInterval


class Evidence(Contract):
    evidence_id: NonEmpty
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    kind: Literal["observation", "document_chunk", "assertion"]
    text: str = Field(max_length=4000)
    interval: TimeInterval | None
    published_at: AwareDatetime | None
    available_at: AwareDatetime | None
    locator: EvidenceLocator
    metrics: list[Metric] = Field(max_length=10)
    quality_flags: list[NonEmpty] = Field(max_length=20)


class GraphEdge(Contract):
    edge_id: NonEmpty
    source_service_id: NonEmpty
    target_service_id: NonEmpty
    relationship: Literal["CALLS", "MESSAGE_FLOW"]
    supporting_evidence_ids: list[NonEmpty] = Field(min_length=1, max_length=20)


class GraphPath(Contract):
    path_id: NonEmpty
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    service_ids: list[NonEmpty] = Field(min_length=2, max_length=4)
    edges: list[GraphEdge] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def continuous(self) -> "GraphPath":
        if len(self.edges) != len(self.service_ids) - 1:
            raise ValueError("path edge count does not match services")
        for index, edge in enumerate(self.edges):
            if (edge.source_service_id, edge.target_service_id) != (
                self.service_ids[index],
                self.service_ids[index + 1],
            ):
                raise ValueError("path edge direction is not continuous")
        return self


class GraphNode(Contract):
    service_id: NonEmpty
    service_key: NonEmpty
    display_name: NonEmpty
    quality_flags: list[NonEmpty] = Field(max_length=20)


class GraphResponse(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    nodes: list[GraphNode] = Field(max_length=80)
    edges: list[GraphEdge] = Field(max_length=120)
    truncated: bool
    warnings: list[NonEmpty] = Field(max_length=20)


class KnowledgeNode(Contract):
    node_id: NonEmpty
    kind: Literal["service", "observation", "document", "chunk", "assertion"]
    label: str = Field(min_length=1, max_length=500)
    evidence_ids: list[NonEmpty] = Field(max_length=20)


class KnowledgeEdge(Contract):
    source: NonEmpty
    target: NonEmpty
    relationship: Literal[
        "CALLS",
        "ABOUT_SERVICE",
        "FROM_SERVICE",
        "TO_SERVICE",
        "HAS_CHUNK",
        "MENTIONS",
        "SUPPORTED_BY",
    ]
    evidence_ids: list[NonEmpty] = Field(min_length=1, max_length=20)


class KnowledgeResponse(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    nodes: list[KnowledgeNode] = Field(max_length=160)
    edges: list[KnowledgeEdge] = Field(max_length=256)
    omitted_observations: int = Field(ge=0)
    omitted_chunks: int = Field(ge=0)
    omitted_assertions: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def connected_endpoints(self) -> "KnowledgeResponse":
        ids = {node.node_id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(
            edge.source not in ids or edge.target not in ids for edge in self.edges
        ):
            raise ValueError("knowledge graph has duplicate nodes or missing endpoints")
        return self


class KnowledgeCheck(Contract):
    assertion_id: UUID
    service_id: NonEmpty
    quote: str = Field(min_length=1, max_length=8000)
    document_title: str = Field(min_length=1, max_length=500)
    evidence_id: NonEmpty
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    service_ids: list[NonEmpty] = Field(min_length=1, max_length=4)
    call_edges: list[GraphEdge] = Field(max_length=3)

    @model_validator(mode="after")
    def valid_chain(self) -> "KnowledgeCheck":
        if (
            self.end_char - self.start_char != len(self.quote)
            or self.service_id != self.service_ids[-1]
            or len(set(self.service_ids)) != len(self.service_ids)
        ):
            raise ValueError("invalid check source or target")
        if len(self.call_edges) != len(self.service_ids) - 1 or any(
            (edge.source_service_id, edge.target_service_id)
            != (self.service_ids[i], self.service_ids[i + 1])
            for i, edge in enumerate(self.call_edges)
        ):
            raise ValueError("check path direction is not continuous")
        return self


class KnowledgeChecksResponse(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    symptom_service_id: NonEmpty
    checks: list[KnowledgeCheck] = Field(max_length=12)
    truncated: bool
    reasoning_version: Literal["directed-runbook-paths-v1"] = "directed-runbook-paths-v1"


class Summary(Contract):
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[NonEmpty] = Field(max_length=12)


class Hypothesis(Contract):
    service_id: NonEmpty
    rank: int = Field(ge=1, le=3)
    claim_ids: list[NonEmpty] = Field(min_length=1, max_length=6)
    supporting_evidence_ids: list[NonEmpty] = Field(min_length=1, max_length=12)
    conflicting_evidence_ids: list[NonEmpty] = Field(max_length=12)
    diagnostic_check_ids: list[NonEmpty] = Field(max_length=5)


class Claim(Contract):
    claim_id: NonEmpty
    claim_type: Literal["observation", "numeric", "relationship", "hypothesis", "limitation"]
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[NonEmpty] = Field(max_length=12)
    metric: Metric | None
    path_id: NonEmpty | None

    @model_validator(mode="after")
    def typed_support(self) -> "Claim":
        if self.claim_type == "numeric" and self.metric is None:
            raise ValueError("numeric claim requires metric")
        if self.claim_type == "relationship" and self.path_id is None:
            raise ValueError("relationship claim requires path_id")
        return self


class Narrative(Contract):
    status: Literal["supported_hypotheses", "insufficient_evidence", "no_anomaly_detected"]
    summary: Summary
    hypotheses: list[Hypothesis] = Field(max_length=3)
    claims: list[Claim] = Field(max_length=15)
    limitations: list[Annotated[str, StringConstraints(max_length=500)]] = Field(max_length=15)

    @model_validator(mode="after")
    def status_matches_hypotheses(self) -> "Narrative":
        if self.status == "supported_hypotheses" and not self.hypotheses:
            raise ValueError("supported status requires hypotheses")
        if self.status != "supported_hypotheses" and self.hypotheses:
            raise ValueError("non-supported status cannot contain hypotheses")
        return self


class FeatureContribution(Contract):
    feature: NonEmpty
    standardized_contribution: FiniteFloat
    evidence_ids: list[NonEmpty] = Field(max_length=40)


class RankedScore(Contract):
    service_id: NonEmpty
    rank: int = Field(ge=1, le=3)
    score: FiniteFloat
    score_type: Literal["heuristic_score", "linear_decision_score"]
    feature_contributions: list[FeatureContribution] = Field(default_factory=list, max_length=24)


class ResultProvenance(Contract):
    template_version: NonEmpty | None = None
    input_manifest_hash: Sha256
    config_hash: Sha256
    prompt_hash: Sha256 | None
    model_digest: NonEmpty | None
    embedding_revision: NonEmpty
    feature_schema_version: NonEmpty


class Usage(Contract):
    prompt_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)


class InvestigationResult(Contract):
    schema_version: Literal["1.0"]
    investigation_id: UUID
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    retrieval_mode: RetrievalMode
    ranking_mode: RankingMode
    generation_origin: GenerationOrigin
    is_replayed: bool
    fallback_reason: NonEmpty | None
    narrative: Narrative
    evidence: list[Evidence] = Field(max_length=12)
    paths: list[GraphPath] = Field(max_length=3)
    ranked_scores: list[RankedScore] = Field(max_length=3)
    provenance: ResultProvenance
    timings_ms: dict[str, Annotated[FiniteFloat, Field(ge=0)]]
    usage: Usage
    warnings: list[NonEmpty] = Field(max_length=20)

    @model_validator(mode="after")
    def scoped_references(self) -> "InvestigationResult":
        evidence_ids = [item.evidence_id for item in self.evidence]
        claim_ids = [item.claim_id for item in self.narrative.claims]
        path_ids = [item.path_id for item in self.paths]
        for identifiers in (evidence_ids, claim_ids, path_ids):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("duplicate result identifier")
        evidence_set, claim_set, path_set = set(evidence_ids), set(claim_ids), set(path_ids)
        if not set(self.narrative.summary.evidence_ids) <= evidence_set:
            raise ValueError("summary cites unknown evidence")
        for item in self.evidence:
            if (item.workspace_id, item.episode_id, item.snapshot_id) != (
                self.workspace_id,
                self.episode_id,
                self.snapshot_id,
            ):
                raise ValueError("evidence scope differs from result")
            if item.interval and item.interval.end > self.as_of:
                raise ValueError("result cites future observation")
            if item.published_at and item.published_at > self.as_of:
                raise ValueError("result cites future document")
            if self.temporal_mode is TemporalMode.AVAILABLE:
                if item.available_at is None or item.available_at > self.as_of:
                    raise ValueError("result cites unavailable evidence")
        for path in self.paths:
            if (path.workspace_id, path.episode_id, path.snapshot_id, path.as_of) != (
                self.workspace_id,
                self.episode_id,
                self.snapshot_id,
                self.as_of,
            ):
                raise ValueError("path scope or cutoff differs from result")
            for edge in path.edges:
                if not set(edge.supporting_evidence_ids) <= evidence_set:
                    raise ValueError("path cites unknown supporting evidence")
        for claim in self.narrative.claims:
            if not set(claim.evidence_ids) <= evidence_set:
                raise ValueError("claim cites unknown evidence")
            if claim.path_id and claim.path_id not in path_set:
                raise ValueError("claim cites unknown path")
        for hypothesis in self.narrative.hypotheses:
            if not set(hypothesis.claim_ids) <= claim_set:
                raise ValueError("hypothesis cites unknown claim")
            if not set(hypothesis.supporting_evidence_ids) <= evidence_set:
                raise ValueError("hypothesis cites unknown supporting evidence")
            if not set(hypothesis.conflicting_evidence_ids) <= evidence_set:
                raise ValueError("hypothesis cites unknown conflicting evidence")
        return self


class RetrievalRequest(Contract):
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    question: str = Field(min_length=1, max_length=2000)
    symptom_service_id: NonEmpty | None
    mode: RetrievalMode
    max_results: int = Field(ge=1, le=40)


class RetrievalHit(Contract):
    evidence: Evidence
    rank: int = Field(ge=1)
    score: FiniteFloat
    score_components: dict[str, FiniteFloat]


class RetrievalResult(Contract):
    request: RetrievalRequest
    hits: list[RetrievalHit] = Field(max_length=60)
    truncated: bool
    warnings: list[NonEmpty] = Field(max_length=20)


class EvidenceBundle(Contract):
    request: InvestigationRequest
    workspace_id: UUID
    snapshot_id: UUID
    evidence: list[Evidence] = Field(max_length=12)
    paths: list[GraphPath] = Field(max_length=3)
    ranked_scores: list[RankedScore] = Field(max_length=3)
    eligible_hash: Sha256


class EvaluationManifest(Contract):
    schema_version: Literal["1.0"]
    dataset_hash: Sha256
    split_hash: Sha256
    origin: DataOrigin
    evaluator_version: NonEmpty
    query_judgments_hash: Sha256 | None
    target_labels_hash: Sha256 | None
    created_at: AwareDatetime


class EvaluationReport(Contract):
    report_id: UUID
    origin: DataOrigin
    dataset_hash: Sha256
    split_hash: Sha256
    config_hash: Sha256
    model_digest: NonEmpty | None
    sample_size: int = Field(ge=0)
    metrics: dict[str, FiniteFloat | None]
    created_at: AwareDatetime
    published_at: AwareDatetime | None


class EpisodeView(Contract):
    default_as_of: AwareDatetime | None = None
    default_symptom_service_id: Sha256 | None = None
    episode_id: UUID
    display_name: str
    origin: DataOrigin
    event_interval: TimeInterval
    ingestion_status: str
    active_snapshot_id: UUID | None
    created_at: AwareDatetime
    availability_recorded: bool
    service_count: int = Field(ge=0)
    valid_span_count: int = Field(ge=0)
    baseline_interval: TimeInterval | None
    quality_flags: list[str]
    last_investigation_status: str | None


class EpisodePage(Contract):
    items: list[EpisodeView] = Field(max_length=100)
    next_cursor: str | None


class JobView(Job):
    episode_id: UUID | None
    result_id: UUID | None


class InvestigationSubmission(Contract):
    investigation_id: UUID
    job_id: UUID
    status: JobStatus


class ImportSubmissionView(Contract):
    episode_id: UUID
    job_id: UUID
    status: JobStatus


class InvestigationView(Contract):
    investigation_id: UUID
    episode_id: UUID
    snapshot_id: UUID | None
    job_id: UUID | None
    status: JobStatus
    result: InvestigationResult | None
    error: PublicError | None
    created_at: AwareDatetime


class InvestigationPage(Contract):
    items: list[InvestigationView] = Field(max_length=100)
    next_cursor: str | None


class SeriesPoint(Contract):
    service_id: NonEmpty
    observation_id: UUID
    start_ns: Annotated[str, StringConstraints(pattern=r"^[0-9]+$")]
    end_ns: Annotated[str, StringConstraints(pattern=r"^[0-9]+$")]
    sample_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    p50_ms: FiniteFloat | None
    p95_ms: FiniteFloat | None


class SeriesResponse(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    points: list[SeriesPoint] = Field(max_length=2000)
    truncated: bool


class EvaluationPage(Contract):
    items: list[EvaluationReport] = Field(max_length=100)
    next_cursor: str | None


class ApiCapabilities(Contract):
    config_hash: Sha256
    ranking_modes: list[RankingMode]
    public_read_only: bool
    mutations_enabled: bool
    generation_modes: list[GenerationMode]
    maximum_question_characters: int
    maximum_graph_hops: int
    maximum_upload_file_bytes: int
    maximum_upload_bundle_bytes: int
    review_origin: Literal["ai"] = "ai"
    independent_human_review: Literal[False] = False
    limitations: list[str]


class HealthStatus(Contract):
    status: Literal["alive", "ready", "unavailable"]
    generation_degraded: bool = False


class AuthStatus(Contract):
    authorized: bool
    mutations_enabled: bool
