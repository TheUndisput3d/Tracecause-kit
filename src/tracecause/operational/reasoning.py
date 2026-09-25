"""Bounded joins over source-verified operational facts, calls and measurements.

This module cannot query a database or read an evaluation label. Its caller supplies
facts verified against immutable source artifacts and a cutoff-scoped graph. The
temporal joins below identify review priorities, never causes or probabilities.
"""

from __future__ import annotations

import calendar
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from tracecause.contracts.models import GraphEdge, GraphResponse, TemporalMode
from tracecause.investigation.ranking import is_anomalous
from tracecause.operational.reasoning_models import (
    ChangeExplanation,
    ChangeReasoningResponse,
    DownstreamDegradation,
    HistoricalEvidence,
    HistoricalMatch,
    HistoricalReasoningResponse,
    ObservedSignal,
    StateDifference,
)
from tracecause.telemetry.signals import (
    ANOMALY_THRESHOLD,
    BUCKET_NS,
    MIN_BASELINE_BUCKETS,
    MIN_LATENCY_SAMPLES,
    ComputedObservation,
    SignalFeature,
    robust_deviation,
)

if TYPE_CHECKING:
    from tracecause.operational.models import OperationalFact

MAX_FACTS = 2000
MAX_HISTORY = 32
MAX_CHANGES = 24
LOOKBACK_SECONDS = 86400
Scalar = str | int | float | bool | None


def _ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reasoning timestamps must carry an explicit timezone")
    return calendar.timegm(value.utctimetuple()) * 1_000_000_000 + value.microsecond * 1000


def _eligible_facts(
    graph: GraphResponse, facts: Sequence[OperationalFact]
) -> tuple[OperationalFact, ...]:
    if len(facts) > MAX_FACTS:
        raise ValueError("operational reasoning fact limit exceeded")
    seen: set[UUID] = set()
    eligible = []
    for fact in facts:
        if fact.episode_id != graph.episode_id or fact.snapshot_id != graph.snapshot_id:
            raise ValueError("operational fact is outside the graph scope")
        if fact.event.id in seen or fact.evidence_id != f"operation:{fact.event.id}":
            raise ValueError("operational evidence identity is invalid or duplicated")
        seen.add(fact.event.id)
        if fact.event.occurred_at > graph.as_of:
            continue
        if graph.temporal_mode is TemporalMode.AVAILABLE and fact.event.available_at > graph.as_of:
            continue
        eligible.append(fact)
    return tuple(eligible)


def _latest_signals(
    graph: GraphResponse, signals: Sequence[SignalFeature]
) -> dict[str, SignalFeature]:
    if len(signals) > 2400:
        raise ValueError("operational signal limit exceeded")
    visible = {node.service_id for node in graph.nodes}
    latest: dict[str, SignalFeature] = {}
    for signal in sorted(
        signals, key=lambda s: (s.analysis_end_ns, str(s.analysis_observation_id))
    ):
        if signal.service_key in visible and signal.analysis_end_ns <= _ns(graph.as_of):
            latest[signal.service_key] = signal
    return latest


def _directed_paths(graph: GraphResponse, symptom: str) -> dict[str, list[GraphEdge]]:
    visible = {node.service_id for node in graph.nodes}
    if symptom not in visible:
        raise ValueError("symptom service is not visible at this cutoff")
    outgoing: dict[str, list[GraphEdge]] = {}
    for edge in sorted(
        graph.edges, key=lambda e: (e.source_service_id, e.target_service_id, e.edge_id)
    ):
        if edge.source_service_id not in visible or edge.target_service_id not in visible:
            raise ValueError("graph edge endpoint is outside the visible graph")
        outgoing.setdefault(edge.source_service_id, []).append(edge)
    paths: dict[str, list[GraphEdge]] = {symptom: []}
    pending = deque([symptom])
    while pending:
        current = pending.popleft()
        if len(paths[current]) == 3:
            continue
        for edge in outgoing.get(current, []):
            if edge.target_service_id not in paths:
                paths[edge.target_service_id] = [*paths[current], edge]
                pending.append(edge.target_service_id)
    return paths


def _degraded(signal: SignalFeature) -> bool:
    return (
        signal.baseline_bucket_count >= MIN_BASELINE_BUCKETS
        and signal.baseline_sample_count >= MIN_LATENCY_SAMPLES
        and any(
            value is not None and value >= ANOMALY_THRESHOLD
            for value in (signal.latency_deviation_z, signal.error_excess_z)
        )
    )


def degradation_onsets(
    signals: Sequence[SignalFeature], observations: Sequence[ComputedObservation]
) -> dict[str, int | None]:
    """Recompute latency/error onset; volume alone is not evidence of degradation."""
    by_id = {row.id: row for row in observations}
    result: dict[str, int | None] = {}

    for signal in sorted(
        signals, key=lambda s: (s.analysis_end_ns, str(s.analysis_observation_id))
    ):
        result[signal.service_key] = None
        baseline = [by_id[k] for k in signal.baseline_observation_ids if k in by_id]
        if any(row.service_key != signal.service_key for row in baseline):
            raise ValueError("degradation baseline includes another service")
        populated = [row for row in baseline if row.sample_count > 0]
        if (
            len(populated) < MIN_BASELINE_BUCKETS
            or sum(row.sample_count for row in populated) < MIN_LATENCY_SAMPLES
        ):
            continue
        latency = [row.p95_ms for row in populated if row.p95_ms is not None]
        errors = [row.error_count / row.sample_count for row in populated]
        buckets = [by_id[k] for k in signal.analysis_bucket_ids if k in by_id]
        for row in sorted(buckets, key=lambda b: (b.start_ns, str(b.id))):
            if (
                row.service_key != signal.service_key
                or row.sample_count <= 0
                or row.start_ns < max(signal.analysis_start_ns, signal.baseline_end_ns)
                or row.end_ns > signal.analysis_end_ns
            ):
                continue
            deviations = (
                robust_deviation(row.p95_ms, latency, 5.0),
                robust_deviation(row.error_count / row.sample_count, errors, 0.01),
            )
            if any(value is not None and value >= ANOMALY_THRESHOLD for value in deviations):
                result[signal.service_key] = row.start_ns
                break
    return result


def _observed(signal: SignalFeature | None, onset: int | None) -> ObservedSignal | None:
    if signal is None:
        return None
    return ObservedSignal(
        observation_evidence_id=f"observation:{signal.analysis_observation_id}",
        baseline_evidence_ids=[f"observation:{v}" for v in signal.baseline_observation_ids],
        first_anomalous_bucket_ns=(
            str(signal.first_anomalous_bucket_ns)
            if signal.first_anomalous_bucket_ns is not None
            else None
        ),
        first_degradation_bucket_ns=str(onset) if onset is not None else None,
        latency_deviation_z=signal.latency_deviation_z,
        error_excess_z=signal.error_excess_z,
        volume_deviation_z=signal.volume_deviation_z,
        anomalous=is_anomalous(signal),
        degradation_detected=_degraded(signal),
        baseline_bucket_count=signal.baseline_bucket_count,
        baseline_sample_count=signal.baseline_sample_count,
    )


def reason_changes(
    graph: GraphResponse,
    facts: Sequence[OperationalFact],
    signals: Sequence[SignalFeature],
    symptom_service_id: str,
    *,
    degradation_onset_ns: Mapping[str, int | None] | None = None,
) -> ChangeReasoningResponse:
    """Join recent changes to downstream services and measured anomaly onset.

    A change strictly before the first anomalous bucket can receive review priority.
    A change inside that ten-second bucket has ambiguous temporal ordering. Later,
    disconnected and healthy-service changes remain visible as contrasting evidence.
    """
    paths = _directed_paths(graph, symptom_service_id)
    latest = _latest_signals(graph, signals)
    cutoff_ns = _ns(graph.as_of)
    onsets: dict[str, int | None] = {}
    for key, feature in latest.items():
        onsets[key] = None
        if degradation_onset_ns is not None:
            onsets[key] = degradation_onset_ns.get(key)
        elif (
            feature.volume_deviation_z is None
            or abs(feature.volume_deviation_z) < ANOMALY_THRESHOLD
        ):
            onsets[key] = feature.first_anomalous_bucket_ns
    downstream: dict[str, list[DownstreamDegradation]] = {}
    for source in paths:
        descendants = _directed_paths(graph, source)
        proofs = []
        for target in sorted(descendants):
            edges = descendants[target]
            measurement = latest.get(target)
            if not edges or measurement is None or not _degraded(measurement):
                continue
            observed = _observed(measurement, onsets.get(target))
            assert observed is not None
            proofs.append(
                DownstreamDegradation(
                    service_id=target,
                    service_ids=[source, *(edge.target_service_id for edge in edges)],
                    edges=edges,
                    signal=observed,
                )
            )
        downstream[source] = proofs
    changes = []
    for fact in _eligible_facts(graph, facts):
        event = fact.event
        occurred_ns = _ns(event.occurred_at)
        if event.kind not in {"deployment", "configuration"}:
            continue
        if occurred_ns < cutoff_ns - LOOKBACK_SECONDS * 1_000_000_000:
            continue
        signal = latest.get(fact.service_key)
        onset = onsets.get(fact.service_key)
        path = paths.get(fact.service_key)
        association = "outside_dependency_path"
        priority = "context_only"
        explanation = (
            "No directed path of at most three observed dependency steps connects "
            "the selected service to this changed service. This change is context, "
            "not a dependency-linked lead."
        )
        if path is not None:
            if signal is None or not _degraded(signal):
                association = "reachable_without_detected_degradation"
                explanation = (
                    "The changed service is reachable, but its eligible measurements do not "
                    "establish a detected degradation. Missing baselines can limit detection."
                )
            elif onset is None:
                association = "unknown_onset"
                explanation = (
                    "The changed service is reachable and has anomalous measurements, but "
                    "an onset bucket is unavailable, so temporal precedence is unknown."
                )
            elif occurred_ns < onset:
                association = "precedes_observed_degradation"
                priority = "review_first"
                explanation = (
                    "This source-recorded change predates the first detected anomalous "
                    "bucket on a reachable service. Inspect the change and measurements; "
                    "temporal order and dependency reachability do not prove causation."
                )
            elif occurred_ns < onset + BUCKET_NS:
                association = "overlaps_onset_bucket"
                priority = "timing_ambiguous"
                explanation = (
                    "The change falls inside the first anomalous ten-second bucket. "
                    "These aggregates cannot establish whether the change or degradation "
                    "occurred first."
                )
            else:
                association = "after_observed_degradation"
                explanation = (
                    "The change follows the first detected anomalous bucket. It does not "
                    "explain that earlier onset; it may still be operationally relevant."
                )
        changes.append(
            ChangeExplanation.model_validate(
                dict(
                    event_id=event.id,
                    kind=event.kind,
                    service_id=fact.service_key,
                    occurred_at=event.occurred_at,
                    evidence_id=fact.evidence_id,
                    association=association,
                    priority=priority,
                    explanation=explanation,
                    service_ids=(
                        [symptom_service_id, *(edge.target_service_id for edge in path)]
                        if path is not None
                        else []
                    ),
                    call_edges=path or [],
                    signal=_observed(signal, onset),
                    downstream_degradation_count=len(downstream.get(fact.service_key, [])),
                    downstream_degradations=downstream.get(fact.service_key, [])[:8],
                )
            )
        )
    tiers = {"review_first": 0, "timing_ambiguous": 1, "context_only": 2}
    # A terminal observed degradation receives review priority over an upstream
    # symptom with degraded descendants. This is a declared localization heuristic,
    # not evidence that every degraded descendant caused its caller's symptoms.
    changes.sort(
        key=lambda c: (
            tiers[c.priority],
            c.downstream_degradation_count > 0 if c.priority == "review_first" else False,
            -_ns(c.occurred_at),
            len(c.call_edges),
            str(c.event_id),
        )
    )
    return ChangeReasoningResponse(
        symptom_service_id=symptom_service_id,
        lookback_seconds=LOOKBACK_SECONDS,
        changes=changes[:MAX_CHANGES],
        omitted_changes=max(0, len(changes) - MAX_CHANGES),
        graph_truncated=graph.truncated,
        limitations=[
            "Review priorities are temporal associations, not causal attribution or probabilities.",
            "Only the preceding 24 hours and at most three directed dependency hops "
            "are considered.",
            "Onset has ten-second bucket precision; missing baselines limit anomaly detection.",
            "Volume changes are shown as context; only latency or error deviations "
            "establish degradation.",
            "A missing or truncated graph path is not proof of operational independence.",
            "Within prior changes, services with no further observed degraded dependencies "
            "are reviewed first. Concurrent faults and incomplete graphs can defeat "
            "this heuristic.",
        ],
    )


@dataclass(frozen=True)
class EpisodeProfile:
    workspace_id: UUID
    application_id: str
    environment: str
    display_name: str
    started_at: datetime
    ended_at: datetime
    available_at: datetime | None
    graph: GraphResponse
    facts: tuple[OperationalFact, ...]
    signals: tuple[SignalFeature, ...]
    degradation_onset_ns: Mapping[str, int | None] | None = None
    knowledge_as_of: datetime | None = None


def _jaccard[T](left: set[T], right: set[T]) -> float | None:
    return len(left & right) / len(left | right) if left or right else None


def _signal_signature(signals: dict[str, SignalFeature]) -> set[tuple[str, str]]:
    result = set()
    for key, signal in signals.items():
        for name, value in (
            ("latency", signal.latency_deviation_z),
            ("errors", signal.error_excess_z),
            ("volume", signal.volume_deviation_z),
        ):
            magnitude = abs(value) if value is not None and name == "volume" else value
            if magnitude is not None and magnitude >= ANOMALY_THRESHOLD:
                result.add((key, name))
    return result


def _source(profile: EpisodeProfile, evidence_id: str) -> HistoricalEvidence:
    return HistoricalEvidence(
        episode_id=profile.graph.episode_id,
        snapshot_id=profile.graph.snapshot_id,
        as_of=profile.graph.as_of,
        temporal_mode=profile.graph.temporal_mode,
        knowledge_as_of=profile.knowledge_as_of or profile.graph.as_of,
        evidence_id=evidence_id,
    )


def _state(
    profile: EpisodeProfile,
) -> dict[tuple[str, str], tuple[Scalar, HistoricalEvidence]]:
    state: dict[tuple[str, str], tuple[Scalar, HistoricalEvidence]] = {}
    for fact in sorted(
        _eligible_facts(profile.graph, profile.facts),
        key=lambda f: (f.event.occurred_at, str(f.event.id)),
    ):
        details = fact.event.details
        fields = []
        if fact.event.kind == "deployment" and "version" in details:
            fields.append(("deployment.version", details["version"]))
        elif fact.event.kind == "configuration" and isinstance(details.get("key"), str):
            if "value" in details:
                fields.append(("configuration." + str(details["key"]), details["value"]))
        elif fact.event.kind == "runtime" and "host" in details:
            fields.append(("runtime.host", details["host"]))
        elif fact.event.kind == "ownership" and "team" in details:
            fields.append(("ownership.team", details["team"]))
        for field, value in fields:
            state[(fact.service_key, field)] = (value, _source(profile, fact.evidence_id))
    return state


def _state_differences(current: EpisodeProfile, previous: EpisodeProfile) -> list[StateDifference]:
    now, before = _state(current), _state(previous)
    result = []
    for key in sorted(now.keys() | before.keys()):
        current_item, previous_item = now.get(key), before.get(key)
        if current_item and previous_item:
            if current_item[0] == previous_item[0] and type(current_item[0]) is type(
                previous_item[0]
            ):
                continue
            status = "changed"
        else:
            status = "previous_unknown" if current_item else "current_unknown"
        result.append(
            StateDifference.model_validate(
                dict(
                    service_id=key[0],
                    field=key[1],
                    status=status,
                    previous_value=previous_item[0] if previous_item else None,
                    current_value=current_item[0] if current_item else None,
                    previous_evidence=previous_item[1] if previous_item else None,
                    current_evidence=current_item[1] if current_item else None,
                )
            )
        )
    return result


def compare_history(
    current: EpisodeProfile,
    previous: Sequence[EpisodeProfile],
    *,
    as_of: datetime,
    temporal_mode: TemporalMode,
) -> HistoricalReasoningResponse:
    """Compare earlier completed episodes within one application and environment.

    Similarity is 20% service overlap, 40% directed dependency overlap and 40%
    measured anomaly-kind overlap. Empty components are omitted and remaining
    weights normalized. No labels, incident text or deployment outcome enter score.
    """
    if len(previous) > MAX_HISTORY:
        raise ValueError("historical reasoning episode limit exceeded")
    if current.graph.as_of != as_of or current.graph.temporal_mode != temporal_mode:
        raise ValueError("current history profile does not match requested cutoff and mode")
    _eligible_facts(current.graph, current.facts)
    current_services = {node.service_id for node in current.graph.nodes}
    current_edges = {
        (e.source_service_id, e.target_service_id, e.relationship): e for e in current.graph.edges
    }
    current_signals = _latest_signals(current.graph, current.signals)
    current_signature = _signal_signature(current_signals)
    seen: set[UUID] = set()
    matches = []
    for prior in previous:
        if prior.graph.episode_id in seen:
            raise ValueError("historical episode occurs more than once")
        seen.add(prior.graph.episode_id)
        if (
            prior.workspace_id != current.workspace_id
            or prior.application_id != current.application_id
            or prior.environment != current.environment
            or prior.graph.episode_id == current.graph.episode_id
            or prior.ended_at > min(current.started_at, as_of)
            or prior.graph.as_of > min(prior.ended_at, as_of)
            or (
                prior.graph.temporal_mode != temporal_mode
                and not (
                    temporal_mode is TemporalMode.AVAILABLE
                    and prior.graph.temporal_mode is TemporalMode.EVENT
                )
            )
        ):
            continue
        if temporal_mode is TemporalMode.AVAILABLE and (
            prior.available_at is None or prior.available_at > as_of
        ):
            continue
        prior = replace(prior, knowledge_as_of=as_of)
        _eligible_facts(prior.graph, prior.facts)
        services = {node.service_id for node in prior.graph.nodes}
        if not current_services & services:
            continue
        edges = {
            (e.source_service_id, e.target_service_id, e.relationship): e for e in prior.graph.edges
        }
        signals = _latest_signals(prior.graph, prior.signals)
        signature = _signal_signature(signals)
        service_similarity = _jaccard(current_services, services)
        assert service_similarity is not None
        dependency_similarity = _jaccard(set(current_edges), set(edges))
        signal_similarity = _jaccard(current_signature, signature)
        components = [(0.2, service_similarity)]
        if dependency_similarity is not None:
            components.append((0.4, dependency_similarity))
        if signal_similarity is not None:
            components.append((0.4, signal_similarity))
        shared_signal_services = {key for key, _ in current_signature & signature}
        differences = _state_differences(current, prior)
        matches.append(
            HistoricalMatch(
                episode_id=prior.graph.episode_id,
                snapshot_id=prior.graph.snapshot_id,
                display_name=prior.display_name,
                ended_at=prior.ended_at,
                as_of=prior.graph.as_of,
                temporal_mode=prior.graph.temporal_mode,
                knowledge_as_of=as_of,
                similarity=sum(weight * value for weight, value in components)
                / sum(weight for weight, _ in components),
                service_similarity=service_similarity,
                dependency_similarity=dependency_similarity,
                signal_similarity=signal_similarity,
                shared_service_ids=sorted(current_services & services),
                new_dependencies=[
                    current_edges[key] for key in sorted(current_edges.keys() - edges.keys())
                ],
                previous_only_dependencies=[
                    edges[key] for key in sorted(edges.keys() - current_edges.keys())
                ],
                state_differences=differences[:80],
                matching_signal_evidence=[
                    _source(prior, f"observation:{signals[key].analysis_observation_id}")
                    for key in sorted(shared_signal_services)
                ],
                current_signal_evidence=[
                    _source(current, f"observation:{current_signals[key].analysis_observation_id}")
                    for key in sorted(shared_signal_services)
                ],
                truncated=current.graph.truncated or prior.graph.truncated or len(differences) > 80,
            )
        )
    matches.sort(key=lambda m: (-m.similarity, -_ns(m.ended_at), str(m.episode_id)))
    return HistoricalReasoningResponse(
        matches=matches[:6],
        eligible_episodes=len(matches),
        omitted_matches=max(0, len(matches) - 6),
        limitations=[
            "Similarity is an explicit weighted overlap, not a probability or causal diagnosis.",
            "Only completed earlier episodes in the same application and environment qualify.",
            "Unobserved configuration or version values remain unknown, not removed or unchanged.",
            "Dependency differences describe captured calls; traffic and sampling can change them.",
            "For available-time replay, a prior incident's final event-time view is used "
            "only after all its source records were available by the current knowledge cutoff.",
        ],
    )
