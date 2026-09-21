"""Public, inspectable results of deterministic operational graph reasoning."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, FiniteFloat

from tracecause.contracts.models import (
    Contract,
    EpochNanoseconds,
    GraphEdge,
    NonEmpty,
    TemporalMode,
)


class ObservedSignal(Contract):
    observation_evidence_id: NonEmpty
    baseline_evidence_ids: list[NonEmpty] = Field(max_length=120)
    first_anomalous_bucket_ns: EpochNanoseconds | None
    first_degradation_bucket_ns: EpochNanoseconds | None
    latency_deviation_z: FiniteFloat | None
    error_excess_z: FiniteFloat | None
    volume_deviation_z: FiniteFloat | None
    anomalous: bool
    degradation_detected: bool
    baseline_bucket_count: int = Field(ge=0)
    baseline_sample_count: int = Field(ge=0)


class DownstreamDegradation(Contract):
    service_id: NonEmpty
    service_ids: list[NonEmpty] = Field(min_length=2, max_length=4)
    edges: list[GraphEdge] = Field(min_length=1, max_length=3)
    signal: ObservedSignal


class ChangeExplanation(Contract):
    event_id: UUID
    kind: Literal["deployment", "configuration"]
    service_id: NonEmpty
    occurred_at: AwareDatetime
    evidence_id: NonEmpty
    association: Literal[
        "precedes_observed_degradation",
        "overlaps_onset_bucket",
        "after_observed_degradation",
        "reachable_without_detected_degradation",
        "outside_dependency_path",
        "unknown_onset",
    ]
    priority: Literal["review_first", "timing_ambiguous", "context_only"]
    explanation: str = Field(min_length=1, max_length=1000)
    service_ids: list[NonEmpty] = Field(max_length=4)
    call_edges: list[GraphEdge] = Field(max_length=3)
    signal: ObservedSignal | None
    downstream_degradation_count: int = Field(ge=0, le=79)
    downstream_degradations: list[DownstreamDegradation] = Field(max_length=8)


class ChangeReasoningResponse(Contract):
    reasoning_version: Literal["operational-temporal-join-v1"] = "operational-temporal-join-v1"
    symptom_service_id: NonEmpty
    lookback_seconds: int = Field(gt=0, le=86400)
    changes: list[ChangeExplanation] = Field(max_length=24)
    omitted_changes: int = Field(ge=0)
    graph_truncated: bool
    limitations: list[str] = Field(max_length=8)


class HistoricalEvidence(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    knowledge_as_of: AwareDatetime
    evidence_id: NonEmpty


class StateDifference(Contract):
    service_id: NonEmpty
    field: str = Field(min_length=1, max_length=240)
    status: Literal["changed", "current_unknown", "previous_unknown"]
    previous_value: str | int | float | bool | None
    current_value: str | int | float | bool | None
    previous_evidence: HistoricalEvidence | None
    current_evidence: HistoricalEvidence | None


class HistoricalMatch(Contract):
    episode_id: UUID
    snapshot_id: UUID
    display_name: str = Field(min_length=1, max_length=120)
    ended_at: AwareDatetime
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    knowledge_as_of: AwareDatetime
    similarity: FiniteFloat = Field(ge=0, le=1)
    service_similarity: FiniteFloat = Field(ge=0, le=1)
    dependency_similarity: FiniteFloat | None = Field(ge=0, le=1)
    signal_similarity: FiniteFloat | None = Field(ge=0, le=1)
    shared_service_ids: list[NonEmpty] = Field(max_length=80)
    new_dependencies: list[GraphEdge] = Field(max_length=120)
    previous_only_dependencies: list[GraphEdge] = Field(max_length=120)
    state_differences: list[StateDifference] = Field(max_length=80)
    matching_signal_evidence: list[HistoricalEvidence] = Field(max_length=160)
    current_signal_evidence: list[HistoricalEvidence] = Field(max_length=160)
    truncated: bool


class HistoricalReasoningResponse(Contract):
    reasoning_version: Literal["operational-history-signature-v1"] = (
        "operational-history-signature-v1"
    )
    matches: list[HistoricalMatch] = Field(max_length=6)
    eligible_episodes: int = Field(ge=0, le=32)
    omitted_matches: int = Field(ge=0)
    limitations: list[str] = Field(max_length=8)
