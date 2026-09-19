"""Deterministic, cutoff-safe measurements derived only from persisted spans."""

from __future__ import annotations

import calendar
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from tracecause.contracts.canonical import semantic_sha256

BUCKET_NS = 10_000_000_000
WINDOW_NS = 60_000_000_000
BASELINE_NS = 300_000_000_000
RULE_VERSION = "trace-signals-v1-http5xx-grpc-nonzero"
MAX_ANCESTOR_HOPS = 3
MAX_HISTORY_OBSERVATIONS = 19_000
MIN_BASELINE_BUCKETS = 6
MIN_LATENCY_SAMPLES = 10
SCORE_CLIP = 10.0
ANOMALY_THRESHOLD = 3.0


@dataclass(frozen=True)
class SpanFact:
    workspace_id: UUID
    episode_id: UUID
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service_key: str | None
    kind: str
    status: str
    start_ns: int
    end_ns: int
    available_at: datetime | None
    safe_attributes: dict[str, str | int | float | bool]
    quality_flags: tuple[str, ...]
    normalized_hash: str
    artifact_hash: str
    source_record_offset: int
    source_line_number: int
    source_resource_index: int
    source_scope_index: int
    source_span_index: int

    @property
    def key(self) -> tuple[str, str]:
        return self.trace_id, self.span_id


@dataclass(frozen=True)
class CallPair:
    caller: SpanFact
    callee: SpanFact
    same_service_ancestors: tuple[SpanFact, ...]


@dataclass(frozen=True)
class ComputedObservation:
    id: UUID
    workspace_id: UUID
    episode_id: UUID
    rule_version: str
    observation_type: Literal["service", "dependency"]
    service_key: str | None
    caller_service_key: str | None
    callee_service_key: str | None
    start_ns: int
    end_ns: int
    sample_count: int
    error_count: int
    p50_ms: float | None
    p95_ms: float | None
    attributes: dict[str, Any]
    provenance_hash: str
    supports: tuple[tuple[str, str, str], ...]
    available_at: datetime | None


@dataclass(frozen=True)
class SignalFeature:
    service_key: str
    analysis_observation_id: UUID
    baseline_observation_ids: tuple[UUID, ...]
    analysis_bucket_ids: tuple[UUID, ...]
    baseline_start_ns: int
    baseline_end_ns: int
    analysis_start_ns: int
    analysis_end_ns: int
    latency_deviation_z: float | None
    error_excess_z: float | None
    volume_deviation_z: float | None
    first_anomalous_bucket_ns: int | None
    completeness: float | None
    baseline_bucket_count: int
    baseline_sample_count: int
    flags: tuple[str, ...]
    formulas: dict[str, str]
    text: str


@dataclass(frozen=True)
class SignalReport:
    workspace_id: UUID
    episode_id: UUID
    rule_version: str
    input_hash: str
    mode: Literal["event", "available"]
    investigation_start_ns: int
    as_of_ns: int
    closed_boundary_ns: int
    baseline_start_ns: int
    observations: tuple[ComputedObservation, ...]
    signals: tuple[SignalFeature, ...]
    source_locators: dict[str, dict[str, Any]]
    quality_flags: tuple[str, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


def _error(span: SpanFact) -> bool:
    if span.status == "ERROR":
        return True
    for key in ("http.response.status_code", "http.status_code"):
        value = span.safe_attributes.get(key)
        if type(value) is int and 500 <= value <= 599:
            return True
    grpc_code = span.safe_attributes.get("rpc.grpc.status_code")
    if type(grpc_code) is int and 1 <= grpc_code <= 16:
        return True
    return False


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = fraction * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _robust_score(value: float | None, reference: list[float], floor: float) -> float | None:
    if value is None or not reference:
        return None
    center = statistics.median(reference)
    mad = statistics.median(abs(item - center) for item in reference)
    return max(-SCORE_CLIP, min(SCORE_CLIP, (value - center) / max(1.4826 * mad, floor)))


def _time_valid(parent: SpanFact, child: SpanFact) -> bool:
    return parent.start_ns <= child.end_ns and parent.end_ns >= child.start_ns


def robust_deviation(value: float | None, reference: list[float], floor: float) -> float | None:
    """Shared version-1 deviation formula for independently typed operational joins."""
    return _robust_score(value, reference, floor)


def _available_ns(value: datetime) -> int:
    utc = value.utctimetuple()
    return calendar.timegm(utc) * 1_000_000_000 + value.microsecond * 1000


def infer_calls(
    spans: tuple[SpanFact, ...], *, max_hops: int = MAX_ANCESTOR_HOPS
) -> tuple[tuple[CallPair, ...], dict[tuple[str, str], str]]:
    """Use trace parentage, at most three same-service ancestors, and overlap."""
    by_id = {span.key: span for span in spans}
    pairs: list[CallPair] = []
    unsupported: dict[tuple[str, str], str] = {}
    seen_children: set[tuple[str, str]] = set()
    for child in spans:
        if child.kind != "SERVER" or child.service_key is None or child.parent_span_id is None:
            continue
        if child.key in seen_children:
            continue
        seen_children.add(child.key)
        cursor = child
        visited = {child.span_id}
        same: list[SpanFact] = []
        for _ in range(max_hops + 1):
            parent_id = cursor.parent_span_id
            if parent_id is None:
                break
            if parent_id in visited:
                unsupported[child.key] = "parent_cycle"
                break
            visited.add(parent_id)
            parent = by_id.get((child.trace_id, parent_id))
            if parent is None:
                unsupported[child.key] = "missing_parent"
                break
            if not _time_valid(parent, cursor):
                unsupported[child.key] = "clock_skew_or_nonoverlap"
                break
            if parent.kind in {"PRODUCER", "CONSUMER"}:
                unsupported[child.key] = "async_parent_unsupported"
                break
            if parent.service_key is None:
                unsupported[child.key] = "unresolved_parent_service"
                break
            if parent.service_key != child.service_key:
                pairs.append(CallPair(parent, child, tuple(same)))
                break
            if len(same) == max_hops:
                unsupported[child.key] = "ancestor_hop_limit"
                break
            same.append(parent)
            cursor = parent
    return tuple(pairs), unsupported


def _subject_key(
    observation_type: str, service: str | None, caller: str | None, callee: str | None
) -> tuple[str, str | None, str | None, str | None]:
    return observation_type, service, caller, callee


def _observation(
    *,
    workspace_id: UUID,
    episode_id: UUID,
    input_hash: str,
    observation_type: Literal["service", "dependency"],
    service_key: str | None,
    caller_service_key: str | None,
    callee_service_key: str | None,
    start_ns: int,
    end_ns: int,
    samples: list[tuple[SpanFact, tuple[SpanFact, ...]]],
    unsupported: dict[tuple[str, str], str],
    bucket: bool,
) -> ComputedObservation:
    durations = [(span.end_ns - span.start_ns) / 1_000_000 for span, _ in samples]
    support_set = {
        (item.trace_id, item.span_id, "measurement" if item == measured else "ancestor")
        for measured, ancestors in samples
        for item in (measured, *ancestors)
    }
    supports = tuple(sorted(support_set))
    sources = [item for measured, ancestors in samples for item in (measured, *ancestors)]
    source_times = [item.available_at for item in sources if item.available_at is not None]
    available_at = max(source_times) if sources and len(source_times) == len(sources) else None
    status_counts = {
        status: sum(span.status == status for span, _ in samples)
        for status in ("UNSET", "OK", "ERROR")
    }
    quality = sorted({flag for span, _ in samples for flag in span.quality_flags})
    quality.extend(
        sorted({unsupported[span.key] for span, _ in samples if span.key in unsupported})
    )
    good_count = sum(
        not span.quality_flags and span.key not in unsupported and span.status != "UNSET"
        for span, _ in samples
    )
    attributes: dict[str, Any] = {
        "bucket": bucket,
        "input_hash": input_hash,
        "status_counts": status_counts,
        "error_fraction": sum(_error(span) for span, _ in samples) / len(samples)
        if samples
        else None,
        "completeness": good_count / len(samples) if samples else None,
        "unset_status_fraction": status_counts["UNSET"] / len(samples) if samples else None,
        "quality_flags": sorted(set(quality)),
        "latency_source": "server",
        "error_rule": (
            "status=ERROR, HTTP 5xx or gRPC status 1..16; UNSET and HTTP 4xx alone are not errors"
        ),
        "quantile_rule": "linear interpolation on sorted eligible raw durations",
    }
    if observation_type == "dependency":
        attributes["caller_span_kinds"] = sorted(
            {parents[0].kind for _, parents in samples if parents}
        )
        client_durations = [
            (parents[0].end_ns - parents[0].start_ns) / 1_000_000
            for _, parents in samples
            if parents and parents[0].kind == "CLIENT"
        ]
        attributes["client_observed_count"] = len(client_durations)
        attributes["client_observed_p50_ms"] = _quantile(client_durations, 0.5)
        attributes["client_observed_p95_ms"] = _quantile(client_durations, 0.95)
    identity = {
        "rule_version": RULE_VERSION,
        "input_hash": input_hash,
        "episode_id": str(episode_id),
        "subject": _subject_key(
            observation_type, service_key, caller_service_key, callee_service_key
        ),
        "start_ns": start_ns,
        "end_ns": end_ns,
        "supports": supports,
        "available_at": available_at.isoformat() if available_at else None,
        "attributes": attributes,
    }
    provenance_hash = semantic_sha256(identity)
    observation_id = uuid5(NAMESPACE_URL, f"tracecause:observation:{provenance_hash}")
    return ComputedObservation(
        observation_id,
        workspace_id,
        episode_id,
        RULE_VERSION,
        observation_type,
        service_key,
        caller_service_key,
        callee_service_key,
        start_ns,
        end_ns,
        len(samples),
        sum(_error(span) for span, _ in samples),
        _quantile(durations, 0.5),
        _quantile(durations, 0.95),
        attributes,
        provenance_hash,
        supports,
        available_at,
    )


def _feature(
    service: str,
    analysis: ComputedObservation,
    baseline: list[ComputedObservation],
    analysis_buckets: list[ComputedObservation],
    start_ns: int,
    end_ns: int,
) -> SignalFeature:
    populated = [item for item in baseline if item.sample_count > 0]
    baseline_samples = sum(item.sample_count for item in baseline)
    sufficient = len(populated) >= MIN_BASELINE_BUCKETS and baseline_samples >= MIN_LATENCY_SAMPLES
    flags: list[str] = []
    if not sufficient:
        flags.append("insufficient_baseline")
    if analysis.sample_count == 0:
        flags.append("no_analysis_samples")
    if analysis.attributes["quality_flags"]:
        flags.extend(analysis.attributes["quality_flags"])
    latency_reference = [item.p95_ms for item in populated if item.p95_ms is not None]
    error_reference = [item.attributes["error_fraction"] for item in populated]
    count_reference = [float(item.sample_count) for item in baseline]
    latency_z = _robust_score(analysis.p95_ms, latency_reference, 5.0) if sufficient else None
    error_z = (
        _robust_score(analysis.attributes["error_fraction"], error_reference, 0.01)
        if sufficient
        else None
    )
    volume_z = (
        _robust_score(analysis.sample_count / 6.0, count_reference, 1.0) if sufficient else None
    )
    onset: int | None = None
    if sufficient:
        for bucket in analysis_buckets:
            if bucket.start_ns < start_ns:
                continue
            bucket_latency = _robust_score(bucket.p95_ms, latency_reference, 5.0)
            bucket_error = _robust_score(bucket.attributes["error_fraction"], error_reference, 0.01)
            bucket_count = _robust_score(float(bucket.sample_count), count_reference, 1.0)
            if (
                bucket_latency is not None
                and bucket_latency >= ANOMALY_THRESHOLD
                or bucket_error is not None
                and bucket_error >= ANOMALY_THRESHOLD
                or bucket_count is not None
                and abs(bucket_count) >= ANOMALY_THRESHOLD
            ):
                onset = bucket.start_ns
                break
    formulas = {
        "latency_deviation_z": (
            "clip((window_p95_ms - median(populated baseline bucket p95_ms)) "
            "/ max(1.4826*MAD, 5 ms), -10, 10)"
        ),
        "error_excess_z": (
            "clip((window_error_fraction - median(populated baseline bucket error_fraction)) "
            "/ max(1.4826*MAD, 0.01), -10, 10)"
        ),
        "volume_deviation_z": (
            "clip((window_count/6 - median(all 30 baseline bucket counts)) "
            "/ max(1.4826*MAD, 1 count), -10, 10)"
        ),
        "onset": "first analysis bucket with latency_z>=3, error_z>=3 or abs(volume_z)>=3",
        "completeness": (
            "server spans with known status and no quality/parent issue divided by server count"
        ),
    }
    text = (
        f"Service {service} had {analysis.sample_count} server requests and "
        f"{analysis.error_count} explicit errors in [{analysis.start_ns},{analysis.end_ns}); "
        f"p95={analysis.p95_ms if analysis.p95_ms is not None else 'unknown'} ms; "
        f"baseline {'eligible' if sufficient else 'insufficient'} "
        f"({len(populated)} populated buckets, "
        f"{baseline_samples} requests)."
    )
    return SignalFeature(
        service,
        analysis.id,
        tuple(item.id for item in baseline),
        tuple(item.id for item in analysis_buckets),
        start_ns - BASELINE_NS,
        start_ns,
        end_ns - WINDOW_NS,
        end_ns,
        latency_z,
        error_z,
        volume_z,
        onset,
        analysis.attributes["completeness"],
        len(populated),
        baseline_samples,
        tuple(sorted(set(flags))),
        formulas,
        text,
    )


def compute_signals(
    spans: tuple[SpanFact, ...],
    *,
    workspace_id: UUID,
    episode_id: UUID,
    investigation_start_ns: int,
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
    include_history_buckets: bool = False,
) -> SignalReport:
    """Compute a cutoff; optionally publish intervening buckets for snapshot replay.

    Extra buckets do not change the baseline, analysis window or ranked features.
    Their individual content and identities use the same authoritative inputs.
    """
    if investigation_start_ns <= BASELINE_NS or investigation_start_ns % BUCKET_NS:
        raise ValueError("investigation start must be a positive closed 10-second boundary")
    if as_of_ns < investigation_start_ns or as_of_ns > 2**63 - 1:
        raise ValueError("invalid as-of cutoff")
    if mode not in {"event", "available"}:
        raise ValueError("invalid replay mode")
    boundary = as_of_ns // BUCKET_NS * BUCKET_NS
    baseline_start = investigation_start_ns - BASELINE_NS
    event_candidates = tuple(
        span
        for span in spans
        if span.workspace_id == workspace_id
        and span.episode_id == episode_id
        and baseline_start <= span.end_ns < boundary
    )
    selected = tuple(
        sorted(
            (
                span
                for span in event_candidates
                if (
                    mode == "event"
                    or span.available_at is not None
                    and _available_ns(span.available_at) <= as_of_ns
                )
            ),
            key=lambda item: (item.end_ns, item.trace_id, item.span_id),
        )
    )
    input_hash = semantic_sha256(
        {
            "rule_version": RULE_VERSION,
            "workspace_id": str(workspace_id),
            "episode_id": str(episode_id),
            "start_ns": investigation_start_ns,
            "as_of_ns": as_of_ns,
            "mode": mode,
            "spans": [(item.trace_id, item.span_id, item.normalized_hash) for item in selected],
        }
    )
    pairs, unsupported = infer_calls(selected)
    service_keys = sorted(
        {item.service_key for item in selected if item.service_key and item.kind == "SERVER"}
    )
    dependency_keys = sorted({(pair.caller.service_key, pair.callee.service_key) for pair in pairs})
    observations: list[ComputedObservation] = []
    bucket_by_subject: dict[
        tuple[str, str | None, str | None, str | None], list[ComputedObservation]
    ] = {}
    analysis_start = boundary - WINDOW_NS
    bucket_starts = sorted(
        set(range(baseline_start, investigation_start_ns, BUCKET_NS))
        | set(range(analysis_start, boundary, BUCKET_NS))
        | (
            {span.end_ns // BUCKET_NS * BUCKET_NS for span in selected}
            if include_history_buckets
            else set()
        )
    )
    # A sparse, arbitrarily long uploaded interval must not allocate an unbounded
    # time range. Empty historical buckets have no source support to publish.
    if (
        include_history_buckets
        and (len(bucket_starts) + 1) * (len(service_keys) + len(dependency_keys))
        > MAX_HISTORY_OBSERVATIONS
    ):
        raise ValueError("snapshot history observation limit exceeded")
    for bucket_start in bucket_starts:
        bucket_end = bucket_start + BUCKET_NS
        for service in service_keys:
            service_samples: list[tuple[SpanFact, tuple[SpanFact, ...]]] = [
                (item, ())
                for item in selected
                if item.kind == "SERVER"
                and item.service_key == service
                and bucket_start <= item.end_ns < bucket_end
            ]
            observation = _observation(
                workspace_id=workspace_id,
                episode_id=episode_id,
                input_hash=input_hash,
                observation_type="service",
                service_key=service,
                caller_service_key=None,
                callee_service_key=None,
                start_ns=bucket_start,
                end_ns=bucket_end,
                samples=service_samples,
                unsupported=unsupported,
                bucket=True,
            )
            observations.append(observation)
            bucket_by_subject.setdefault(_subject_key("service", service, None, None), []).append(
                observation
            )
        for caller, callee in dependency_keys:
            dependency_samples = [
                (pair.callee, (pair.caller, *pair.same_service_ancestors))
                for pair in pairs
                if pair.caller.service_key == caller
                and pair.callee.service_key == callee
                and bucket_start <= pair.callee.end_ns < bucket_end
            ]
            observation = _observation(
                workspace_id=workspace_id,
                episode_id=episode_id,
                input_hash=input_hash,
                observation_type="dependency",
                service_key=None,
                caller_service_key=caller,
                callee_service_key=callee,
                start_ns=bucket_start,
                end_ns=bucket_end,
                samples=dependency_samples,
                unsupported=unsupported,
                bucket=True,
            )
            observations.append(observation)
            bucket_by_subject.setdefault(
                _subject_key("dependency", None, caller, callee), []
            ).append(observation)
    features: list[SignalFeature] = []
    for service in service_keys:
        service_samples = [
            (item, ())
            for item in selected
            if item.kind == "SERVER"
            and item.service_key == service
            and analysis_start <= item.end_ns < boundary
        ]
        window = _observation(
            workspace_id=workspace_id,
            episode_id=episode_id,
            input_hash=input_hash,
            observation_type="service",
            service_key=service,
            caller_service_key=None,
            callee_service_key=None,
            start_ns=analysis_start,
            end_ns=boundary,
            samples=service_samples,
            unsupported=unsupported,
            bucket=False,
        )
        observations.append(window)
        buckets = bucket_by_subject[_subject_key("service", service, None, None)]
        features.append(
            _feature(
                service,
                window,
                [
                    item
                    for item in buckets
                    if baseline_start <= item.start_ns < investigation_start_ns
                ],
                [item for item in buckets if analysis_start <= item.start_ns < boundary],
                investigation_start_ns,
                boundary,
            )
        )
    for caller, callee in dependency_keys:
        dependency_samples = [
            (pair.callee, (pair.caller, *pair.same_service_ancestors))
            for pair in pairs
            if pair.caller.service_key == caller
            and pair.callee.service_key == callee
            and analysis_start <= pair.callee.end_ns < boundary
        ]
        observations.append(
            _observation(
                workspace_id=workspace_id,
                episode_id=episode_id,
                input_hash=input_hash,
                observation_type="dependency",
                service_key=None,
                caller_service_key=caller,
                callee_service_key=callee,
                start_ns=analysis_start,
                end_ns=boundary,
                samples=dependency_samples,
                unsupported=unsupported,
                bucket=False,
            )
        )
    locators = {
        f"{item.trace_id}:{item.span_id}": {
            "artifact_hash": item.artifact_hash,
            "trace_id": item.trace_id,
            "span_id": item.span_id,
            "original_source_record_offset": item.source_record_offset,
            "original_source_line_number": item.source_line_number,
            "original_source_resource_index": item.source_resource_index,
            "original_source_scope_index": item.source_scope_index,
            "original_source_span_index": item.source_span_index,
        }
        for item in selected
    }
    quality_flags = sorted(set(unsupported.values()))
    if any(
        item.kind == "CLIENT" and not any(pair.caller.key == item.key for pair in pairs)
        for item in selected
    ):
        quality_flags.append("unpaired_client_callee_unknown")
    if mode == "available" and any(item.available_at is None for item in event_candidates):
        quality_flags.append("missing_availability_excluded")
    return SignalReport(
        workspace_id,
        episode_id,
        RULE_VERSION,
        input_hash,
        mode,
        investigation_start_ns,
        as_of_ns,
        boundary,
        baseline_start,
        tuple(observations),
        tuple(features),
        locators,
        tuple(sorted(set(quality_flags))),
    )
