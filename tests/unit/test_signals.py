"""Hand-calculated trace-derived evidence and temporal adversaries."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tracecause.storage.postgres import closed_analysis_boundary
from tracecause.telemetry.signals import (
    BASELINE_NS,
    BUCKET_NS,
    SignalReport,
    SpanFact,
    compute_signals,
    infer_calls,
)

WORKSPACE = UUID("00000000-0000-4000-8000-000000000001")
EPISODE = UUID("00000000-0000-4000-8000-000000000002")
ZERO = 1_700_000_000_000_000_000
ALERT = ZERO + BASELINE_NS


def fact(
    number: int,
    *,
    service: str = "service-a",
    kind: str = "SERVER",
    status: str = "OK",
    end_ns: int,
    duration_ms: int = 10,
    trace: int | None = None,
    parent: int | None = None,
    attributes: dict[str, str | int | float | bool] | None = None,
    available_at: datetime | None = None,
) -> SpanFact:
    return SpanFact(
        WORKSPACE,
        EPISODE,
        f"{trace if trace is not None else number:032x}",
        f"{number:016x}",
        f"{parent:016x}" if parent is not None else None,
        service,
        kind,
        status,
        end_ns - duration_ms * 1_000_000,
        end_ns,
        available_at,
        attributes or {},
        (),
        hashlib.sha256(str(number).encode()).hexdigest(),
        "a" * 64,
        number,
        number,
        0,
        0,
        0,
    )


def baseline() -> tuple[SpanFact, ...]:
    return tuple(
        fact(index + 1, end_ns=ZERO + index * BUCKET_NS + 5_000_000_000) for index in range(30)
    )


def report(spans: tuple[SpanFact, ...], *, as_of_ns: int = ALERT + 60_000_000_000) -> SignalReport:
    return compute_signals(
        spans,
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=as_of_ns,
    )


def test_hand_calculated_count_errors_quantiles_and_robust_features() -> None:
    spans = baseline() + (
        fact(31, end_ns=ALERT + 5_000_000_000, duration_ms=100, status="ERROR"),
        fact(
            32,
            end_ns=ALERT + 15_000_000_000,
            duration_ms=200,
            status="UNSET",
            attributes={"http.status_code": 404},
        ),
    )
    result = report(spans)
    feature = result.signals[0]
    window = next(
        item for item in result.observations if item.id == feature.analysis_observation_id
    )
    assert window.sample_count == 2 and window.error_count == 1
    assert window.p50_ms == 150 and window.p95_ms == 195
    assert window.attributes["status_counts"] == {"UNSET": 1, "OK": 0, "ERROR": 1}
    assert window.attributes["completeness"] == 0.5
    assert feature.latency_deviation_z == 10 and feature.error_excess_z == 10
    assert feature.volume_deviation_z == pytest.approx(-2 / 3)
    assert feature.first_anomalous_bucket_ns == ALERT
    assert feature.baseline_bucket_count == 30 and feature.baseline_sample_count == 30
    assert len(window.supports) == 2
    assert (
        result.source_locators[f"{spans[-1].trace_id}:{spans[-1].span_id}"]["artifact_hash"]
        == "a" * 64
    )


def test_snapshot_history_preserves_features_and_earlier_bucket_support() -> None:
    early = fact(40, end_ns=ALERT + 35_000_000_000, status="ERROR")
    late = fact(41, end_ns=ALERT + 175_000_000_000)
    spans = baseline() + (early, late)
    cutoff = ALERT + 180_000_000_000
    original = report(spans, as_of_ns=cutoff)
    expanded = compute_signals(
        spans,
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=cutoff,
        include_history_buckets=True,
    )
    assert expanded.signals == original.signals
    assert expanded.input_hash == original.input_hash
    assert set(item.id for item in original.observations) <= {
        item.id for item in expanded.observations
    }
    for item in original.observations:
        assert next(row for row in expanded.observations if row.id == item.id) == item
    middle = next(item for item in expanded.observations if item.start_ns == ALERT + 30_000_000_000)
    assert middle.sample_count == 1 and middle.error_count == 1
    assert middle.supports == ((early.trace_id, early.span_id, "measurement"),)
    assert all(item.end_ns <= cutoff for item in expanded.observations)


def test_snapshot_history_sparse_long_intervals_and_observation_bound(monkeypatch) -> None:
    import tracecause.telemetry.signals as signals

    arguments = dict(
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=ALERT + 1_000_000 * BUCKET_NS,
        include_history_buckets=True,
    )
    sparse = compute_signals(baseline(), **arguments)
    assert len(sparse.observations) == 37
    monkeypatch.setattr(signals, "MAX_HISTORY_OBSERVATIONS", 36)
    with pytest.raises(ValueError, match="history observation limit"):
        compute_signals(baseline(), **arguments)


def test_capture_baseline_excludes_first_intervention_seconds() -> None:
    real_baseline_end = ALERT + 2_000_000_000
    intervention = fact(99, end_ns=ALERT + 3_000_000_000, status="ERROR")
    result = compute_signals(
        baseline() + (intervention,),
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=closed_analysis_boundary(real_baseline_end),
        as_of_ns=ALERT + 60_000_000_000,
    )
    feature = result.signals[0]
    baseline_rows = [
        item for item in result.observations if item.id in feature.baseline_observation_ids
    ]
    assert feature.baseline_end_ns <= real_baseline_end
    assert feature.baseline_sample_count == 30
    assert all(item.error_count == 0 for item in baseline_rows)
    analysis = next(
        item for item in result.observations if item.id == feature.analysis_observation_id
    )
    assert analysis.error_count == 1


def test_window_quantile_uses_raw_durations_not_bucket_p95_average() -> None:
    spans = baseline() + (
        fact(31, end_ns=ALERT + 5_000_000_000, duration_ms=10),
        fact(32, end_ns=ALERT + 5_500_000_000, duration_ms=10),
        fact(33, end_ns=ALERT + 15_000_000_000, duration_ms=100),
        fact(34, end_ns=ALERT + 15_500_000_000, duration_ms=100),
    )
    result = report(spans)
    feature = result.signals[0]
    window = next(
        item for item in result.observations if item.id == feature.analysis_observation_id
    )
    assert window.p95_ms == 100
    buckets = [item for item in result.observations if item.id in feature.analysis_bucket_ids]
    assert sum(item.p95_ms for item in buckets if item.p95_ms is not None) / 2 == 55


def test_parent_child_direction_deduplicated_and_unrelated_trace_not_linked() -> None:
    client = fact(
        1, service="caller", kind="CLIENT", trace=99, end_ns=ALERT + 9_000_000_000, duration_ms=8000
    )
    server = fact(
        2, service="callee", trace=99, parent=1, end_ns=ALERT + 6_000_000_000, duration_ms=1000
    )
    unrelated = fact(3, service="other", trace=99, end_ns=ALERT + 7_000_000_000)
    pairs, unsupported = infer_calls((client, server, unrelated))
    assert len(pairs) == 1 and not unsupported
    assert pairs[0].caller.key == client.key and pairs[0].callee.key == server.key
    result = report(baseline() + (client, server, unrelated))
    dependencies = [
        item
        for item in result.observations
        if item.observation_type == "dependency" and not item.attributes["bucket"]
    ]
    assert len(dependencies) == 1 and dependencies[0].sample_count == 1
    assert dependencies[0].attributes["client_observed_count"] == 1
    assert dependencies[0].attributes["client_observed_p95_ms"] == 8000
    assert {(trace_id, span_id) for trace_id, span_id, _ in dependencies[0].supports} == {
        client.key,
        server.key,
    }
    assert "unpaired_client_callee_unknown" not in result.quality_flags


def test_same_service_traversal_and_unsupported_ancestry() -> None:
    caller = fact(
        1, service="caller", kind="CLIENT", trace=55, end_ns=ALERT + 9_000_000_000, duration_ms=8000
    )
    internal = fact(
        2,
        service="callee",
        kind="INTERNAL",
        trace=55,
        parent=1,
        end_ns=ALERT + 8_000_000_000,
        duration_ms=6000,
    )
    child = fact(
        3, service="callee", trace=55, parent=2, end_ns=ALERT + 6_000_000_000, duration_ms=1000
    )
    missing = fact(4, service="callee", trace=56, parent=99, end_ns=ALERT + 6_000_000_000)
    producer = fact(
        5,
        service="caller",
        kind="PRODUCER",
        trace=57,
        end_ns=ALERT + 9_000_000_000,
        duration_ms=8000,
    )
    async_child = fact(6, service="callee", trace=57, parent=5, end_ns=ALERT + 6_000_000_000)
    pairs, unsupported = infer_calls((caller, internal, child, missing, producer, async_child))
    assert len(pairs) == 1 and pairs[0].same_service_ancestors == (internal,)
    assert unsupported[missing.key] == "missing_parent"
    assert unsupported[async_child.key] == "async_parent_unsupported"
    clock_skewed = fact(
        7,
        service="callee",
        trace=55,
        parent=1,
        end_ns=ALERT + 20_000_000_000,
        duration_ms=1000,
    )
    _, skew_flags = infer_calls((caller, clock_skewed))
    assert skew_flags[clock_skewed.key] == "clock_skew_or_nonoverlap"


def test_empty_baseline_unset_and_http_error_rule() -> None:
    spans = (
        fact(1, status="UNSET", end_ns=ALERT + 5_000_000_000, attributes={"http.status_code": 404}),
        fact(
            2,
            status="UNSET",
            end_ns=ALERT + 15_000_000_000,
            attributes={"http.response.status_code": 503},
        ),
    )
    result = report(spans)
    feature = result.signals[0]
    window = next(
        item for item in result.observations if item.id == feature.analysis_observation_id
    )
    assert window.error_count == 1
    assert window.attributes["status_counts"]["UNSET"] == 2
    assert feature.latency_deviation_z is None and feature.error_excess_z is None
    assert "insufficient_baseline" in feature.flags


def test_grpc_nonzero_is_explicit_error_without_conflating_unset() -> None:
    spans = baseline() + (
        fact(
            31,
            status="UNSET",
            end_ns=ALERT + 5_000_000_000,
            attributes={"rpc.grpc.status_code": 14},
        ),
        fact(
            32,
            status="UNSET",
            end_ns=ALERT + 15_000_000_000,
            attributes={"rpc.grpc.status_code": 0},
        ),
    )
    result = report(spans)
    window = next(item for item in result.observations if not item.attributes["bucket"])
    assert window.error_count == 1
    assert window.attributes["status_counts"]["UNSET"] == 2


def test_low_traffic_baseline_stays_insufficient() -> None:
    sparse = tuple(
        fact(index + 1, end_ns=ZERO + index * BUCKET_NS + 5_000_000_000) for index in range(5)
    )
    result = report(sparse + (fact(31, end_ns=ALERT + 5_000_000_000),))
    feature = result.signals[0]
    assert feature.baseline_bucket_count == 5
    assert feature.latency_deviation_z is None
    assert "insufficient_baseline" in feature.flags


def test_inside_bucket_future_span_and_late_completion_do_not_leak() -> None:
    early = fact(31, end_ns=ALERT + 15_000_000_000)
    future = fact(32, end_ns=ALERT + 25_000_000_000, duration_ms=1000)
    late_completion = fact(33, end_ns=ALERT + 65_000_000_000, duration_ms=50_000)
    a = report(baseline() + (early,), as_of_ns=ALERT + 25_000_000_000)
    b = report(baseline() + (early, future, late_completion), as_of_ns=ALERT + 25_000_000_000)
    assert a == b
    assert a.closed_boundary_ns == ALERT + 20_000_000_000
    assert all(item.end_ns <= a.closed_boundary_ns for item in a.observations)
    assert f"{future.trace_id}:{future.span_id}" not in a.source_locators


def test_available_time_mode_excludes_unavailable_and_late_spans() -> None:
    observed_at = datetime.fromtimestamp((ALERT + 5_000_000_000) / 1e9, UTC)
    observed = fact(
        31,
        end_ns=ALERT + 5_000_000_000,
        available_at=observed_at,
    )
    late = fact(32, end_ns=ALERT + 15_000_000_000, available_at=observed_at + timedelta(minutes=2))
    result = compute_signals(
        (observed, late, *baseline()),
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=ALERT + 60_000_000_000,
        mode="available",
    )
    assert len(result.source_locators) == 1
    assert "missing_availability_excluded" in result.quality_flags
    assert result.signals[0].baseline_bucket_count == 0
    future = fact(33, end_ns=ALERT + 100_000_000_000)
    replay = compute_signals(
        (future, observed, late, *baseline()),
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=ALERT + 60_000_000_000,
        mode="available",
    )
    assert replay == result


def test_duplicate_pair_and_unpaired_client_have_explicit_quality() -> None:
    client = fact(
        1,
        service="caller",
        kind="CLIENT",
        trace=99,
        end_ns=ALERT + 9_000_000_000,
        duration_ms=8000,
    )
    server = fact(
        2,
        service="callee",
        trace=99,
        parent=1,
        end_ns=ALERT + 6_000_000_000,
        duration_ms=1000,
    )
    pairs, _ = infer_calls((client, server, server))
    assert len(pairs) == 1
    unpaired = fact(
        3,
        service="caller",
        kind="CLIENT",
        trace=100,
        end_ns=ALERT + 9_000_000_000,
    )
    result = report(baseline() + (client, server, unpaired))
    assert "unpaired_client_callee_unknown" in result.quality_flags
    dependencies = [
        item
        for item in result.observations
        if item.observation_type == "dependency" and not item.attributes["bucket"]
    ]
    assert len(dependencies) == 1 and dependencies[0].sample_count == 1


def test_recompute_is_stable_and_cross_scope_spans_are_ignored() -> None:
    spans = baseline() + (fact(31, end_ns=ALERT + 5_000_000_000),)
    other = fact(32, end_ns=ALERT + 5_000_000_000)
    other = SpanFact(
        **{**other.__dict__, "episode_id": UUID("00000000-0000-4000-8000-000000000099")}
    )
    a = report(spans)
    b = report((other, *reversed(spans)))
    assert a == b
