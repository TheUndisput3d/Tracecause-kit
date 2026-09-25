"""Versioned, label-free heuristic candidate generation and ranking."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from tracecause.graph.queries import EligibleCall
from tracecause.telemetry.signals import ANOMALY_THRESHOLD, SignalFeature

HEURISTIC_VERSION = "heuristic-v1"
MAX_CANDIDATES = 20


@dataclass(frozen=True)
class CandidateScore:
    service_key: str
    score: float
    latency: float
    error: float
    onset: float
    proximity: float
    anomalous_callers: float
    missingness: float
    anomalous: bool


def is_anomalous(feature: SignalFeature) -> bool:
    return (
        feature.latency_deviation_z is not None
        and feature.latency_deviation_z >= ANOMALY_THRESHOLD
        or feature.error_excess_z is not None
        and feature.error_excess_z >= ANOMALY_THRESHOLD
        or feature.volume_deviation_z is not None
        and abs(feature.volume_deviation_z) >= ANOMALY_THRESHOLD
    )


def generate_candidates(
    signals: tuple[SignalFeature, ...],
    calls: tuple[EligibleCall, ...],
    symptom_service_key: str | None,
) -> tuple[tuple[str, ...], bool]:
    observed = {signal.service_key for signal in signals}
    if symptom_service_key is not None and symptom_service_key not in observed:
        return (), False
    anomalous = sorted(signal.service_key for signal in signals if is_anomalous(signal))
    seeds = tuple(
        dict.fromkeys(
            (*([symptom_service_key] if symptom_service_key is not None else []), *anomalous)
        )
    )
    neighbors: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        if call.caller_service_key in observed and call.callee_service_key in observed:
            neighbors[call.caller_service_key].add(call.callee_service_key)
            neighbors[call.callee_service_key].add(call.caller_service_key)
    ordered = list(seeds)
    seen = set(ordered)
    pending: deque[tuple[str, int]] = deque((key, 0) for key in seeds)
    while pending:
        service, hops = pending.popleft()
        if hops >= 3:
            continue
        for neighbor in sorted(neighbors[service]):
            if neighbor not in seen:
                seen.add(neighbor)
                ordered.append(neighbor)
                pending.append((neighbor, hops + 1))
    return tuple(ordered[:MAX_CANDIDATES]), len(ordered) > MAX_CANDIDATES


def _directed_distances(calls: tuple[EligibleCall, ...], symptom: str | None) -> dict[str, int]:
    if symptom is None:
        return {}
    outgoing: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        outgoing[call.caller_service_key].add(call.callee_service_key)
    distances = {symptom: 0}
    pending = deque([symptom])
    while pending:
        source = pending.popleft()
        if distances[source] >= 3:
            continue
        for target in sorted(outgoing[source]):
            if target not in distances:
                distances[target] = distances[source] + 1
                pending.append(target)
    return distances


def score_candidates(
    signals: tuple[SignalFeature, ...],
    calls: tuple[EligibleCall, ...],
    candidate_keys: tuple[str, ...],
    symptom_service_key: str | None,
) -> tuple[CandidateScore, ...]:
    by_key = {signal.service_key: signal for signal in signals}
    anomalous_onsets = [
        signal.first_anomalous_bucket_ns
        for signal in signals
        if is_anomalous(signal) and signal.first_anomalous_bucket_ns is not None
    ]
    earliest = min(anomalous_onsets) if anomalous_onsets else None
    latest = max(anomalous_onsets) if anomalous_onsets else None
    distances = _directed_distances(calls, symptom_service_key)
    callers: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        callers[call.callee_service_key].add(call.caller_service_key)
    anomalous_keys = {signal.service_key for signal in signals if is_anomalous(signal)}
    output: list[CandidateScore] = []
    for key in candidate_keys:
        signal = by_key.get(key)
        if signal is None:
            continue
        latency = max(0.0, signal.latency_deviation_z or 0.0) / 10.0
        error = max(0.0, signal.error_excess_z or 0.0) / 10.0
        onset = (
            1.0
            if signal.first_anomalous_bucket_ns is not None
            and earliest is not None
            and earliest == latest
            else (
                (latest - signal.first_anomalous_bucket_ns) / (latest - earliest)
                if signal.first_anomalous_bucket_ns is not None
                and earliest is not None
                and latest is not None
                and latest > earliest
                else 0.0
            )
        )
        proximity = 1.0 / (1 + distances[key]) if key in distances else 0.0
        service_callers = callers[key]
        caller_fraction = (
            len(service_callers & anomalous_keys) / len(service_callers) if service_callers else 0.0
        )
        missing = (
            sum(
                (
                    signal.latency_deviation_z is None,
                    signal.error_excess_z is None,
                    signal.first_anomalous_bucket_ns is None,
                    symptom_service_key is None,
                    signal.completeness is None,
                )
            )
            / 5.0
        )
        score = (
            0.30 * latency
            + 0.30 * error
            + 0.15 * onset
            + 0.15 * proximity
            + 0.10 * caller_fraction
            - 0.20 * missing
        )
        output.append(
            CandidateScore(
                key,
                score,
                latency,
                error,
                onset,
                proximity,
                caller_fraction,
                missing,
                is_anomalous(signal),
            )
        )
    return tuple(sorted(output, key=lambda item: (-item.score, item.service_key)))
