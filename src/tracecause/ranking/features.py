"""Production feature extraction shared by offline training and investigations."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass

from tracecause.graph.queries import EligibleCall
from tracecause.investigation.ranking import is_anomalous
from tracecause.telemetry.signals import SignalReport

FEATURE_SCHEMA_VERSION = "candidate-features-v3"
FEATURE_NAMES = (
    "latency_anomaly",
    "error_excess",
    "volume_deviation",
    "onset_gap_seconds",
    "anomalous_caller_fraction",
    "directed_distance",
    "incoming_degree",
    "outgoing_degree",
    "supporting_trace_count",
    "completeness",
    "retrieval_support",
)
TRANSFER_FEATURE_SCHEMA_VERSION = "candidate-features-v4"
TRANSFER_FEATURE_NAMES = (
    *FEATURE_NAMES,
    "error_excess_over_callees",
    "latency_excess_over_callees",
    "positive_error_share",
    "positive_latency_share",
)
_WORDS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class CandidateFeatures:
    service_key: str
    values: tuple[float | None, ...]
    evidence_ids: tuple[str, ...]

    def as_map(self) -> dict[str, float | None]:
        names = (
            TRANSFER_FEATURE_NAMES
            if len(self.values) == len(TRANSFER_FEATURE_NAMES)
            else FEATURE_NAMES
        )
        return dict(zip(names, self.values, strict=True))


def _distances(calls: tuple[EligibleCall, ...], symptom: str | None) -> dict[str, int]:
    if symptom is None:
        return {}
    edges: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        edges[call.caller_service_key].add(call.callee_service_key)
    distance = {symptom: 0}
    pending = deque([symptom])
    while pending:
        source = pending.popleft()
        if distance[source] >= 3:
            continue
        for target in sorted(edges[source]):
            if target not in distance:
                distance[target] = distance[source] + 1
                pending.append(target)
    return distance


def candidate_features(
    report: SignalReport,
    calls: tuple[EligibleCall, ...],
    candidate_keys: tuple[str, ...],
    symptom_service_key: str | None,
    question: str,
    *,
    version: str = FEATURE_SCHEMA_VERSION,
) -> tuple[CandidateFeatures, ...]:
    """Use only report observations and supported calls closed at this cutoff."""
    if version not in {FEATURE_SCHEMA_VERSION, TRANSFER_FEATURE_SCHEMA_VERSION}:
        raise ValueError("unsupported candidate feature schema")
    by_key = {item.service_key: item for item in report.signals}
    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)
    call_support: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        incoming[call.callee_service_key].add(call.caller_service_key)
        outgoing[call.caller_service_key].add(call.callee_service_key)
        references = (
            call.support_observation_ids[:1]
            if version == TRANSFER_FEATURE_SCHEMA_VERSION
            else call.support_observation_ids
        )
        for observation_id in references:
            call_support[call.callee_service_key].add(str(observation_id))
    supporting_traces: dict[str, set[str]] = defaultdict(set)
    for observation in report.observations:
        if (
            observation.observation_type == "service"
            and observation.service_key is not None
            and observation.start_ns >= report.investigation_start_ns
            and observation.end_ns <= report.closed_boundary_ns
        ):
            supporting_traces[observation.service_key].update(
                trace_id for trace_id, _, _ in observation.supports
            )
    distances = _distances(calls, symptom_service_key)
    anomalous = {key for key, signal in by_key.items() if is_anomalous(signal)}
    earliest = min(
        (
            item.first_anomalous_bucket_ns
            for item in report.signals
            if item.first_anomalous_bucket_ns is not None
        ),
        default=None,
    )
    question_words = set(_WORDS.findall(question.lower()))
    output: list[CandidateFeatures] = []
    for key in candidate_keys:
        signal = by_key.get(key)
        if signal is None:
            continue
        callers = incoming[key]
        signal_words = set(_WORDS.findall(signal.text.lower().replace(key.lower(), " ")))
        overlap = (
            len(question_words & signal_words) / len(question_words | signal_words)
            if question_words and signal_words
            else 0.0
        )
        values: tuple[float | None, ...] = (
            signal.latency_deviation_z,
            signal.error_excess_z,
            signal.volume_deviation_z,
            (signal.first_anomalous_bucket_ns - earliest) / 1e9
            if signal.first_anomalous_bucket_ns is not None and earliest is not None
            else None,
            len(callers & anomalous) / len(callers) if callers else 0.0,
            float(distances[key]) if key in distances else None,
            float(len(callers)),
            float(len(outgoing[key])),
            float(len(supporting_traces[key])),
            signal.completeness,
            overlap,
        )
        extra_support: set[str] = set()
        if version == TRANSFER_FEATURE_SCHEMA_VERSION:
            errors = {k: max(0.0, s.error_excess_z or 0.0) for k, s in by_key.items()}
            latencies = {k: max(0.0, s.latency_deviation_z or 0.0) for k, s in by_key.items()}
            children = outgoing[key] & by_key.keys()
            values = (
                *values,
                errors[key] - max((errors[c] for c in children), default=0.0),
                latencies[key] - max((latencies[c] for c in children), default=0.0),
                errors[key] / sum(errors.values()) if sum(errors.values()) else 0.0,
                latencies[key] / sum(latencies.values()) if sum(latencies.values()) else 0.0,
            )
            # Relative features refer to every compared analysis observation and call edge.
            extra_support.update(str(s.analysis_observation_id) for s in report.signals)
            for call in calls:
                if call.caller_service_key == key:
                    extra_support.update(str(value) for value in call.support_observation_ids[:1])
        output.append(
            CandidateFeatures(
                key,
                values,
                tuple(
                    f"observation:{identifier}"
                    for identifier in sorted(
                        {str(signal.analysis_observation_id), *call_support[key], *extra_support}
                    )
                ),
            )
        )
    return tuple(output)
