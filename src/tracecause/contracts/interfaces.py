"""Boundary protocols; implementations arrive in their ordered phases."""

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from tracecause.contracts.models import (
    Evidence,
    EvidenceBundle,
    GenerationMode,
    Narrative,
    RankedScore,
    RetrievalRequest,
    RetrievalResult,
    Snapshot,
)


class FailureCategory(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RESOURCE_LIMIT = "resource_limit"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    PROVIDER_INVALID = "provider_invalid"
    INTERNAL = "internal"


class TraceCauseError(Exception):
    def __init__(self, category: FailureCategory, safe_message: str, retryable: bool = False):
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable


class ArtifactStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, content_hash: str) -> bytes: ...


class MetadataStore(Protocol):
    def get_snapshot(self, workspace_id: UUID, episode_id: UUID, snapshot_id: UUID) -> Snapshot: ...
    def get_evidence(self, workspace_id: UUID, snapshot_id: UUID, evidence_id: str) -> Evidence: ...


class GraphRepository(Protocol):
    def supported_neighbors(
        self, workspace_id: UUID, snapshot_id: UUID, service_id: str, max_hops: int
    ) -> list[str]: ...


class Embedder(Protocol):
    model_revision: str
    dimensions: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class Extractor(Protocol):
    def extract(self, source_text: str, allowed_service_ids: list[str]) -> list[dict[str, str]]: ...


class Generator(Protocol):
    mode: GenerationMode

    def generate(self, bundle: EvidenceBundle) -> Narrative: ...


class CandidateRanker(Protocol):
    feature_schema_version: str

    def rank(self, candidate_features: list[dict[str, float | None]]) -> list[RankedScore]: ...


class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...
