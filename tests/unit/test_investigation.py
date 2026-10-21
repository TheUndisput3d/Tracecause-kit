"""Deterministic candidate bounds and explicit template outcomes."""

from uuid import uuid4

from tracecause.graph.queries import EligibleCall
from tracecause.investigation.ranking import generate_candidates, score_candidates
from tracecause.investigation.templates import template_narrative
from tracecause.telemetry.signals import SignalFeature


def _signal(key: str, *, latency_z: float | None = None) -> SignalFeature:
    return SignalFeature(
        service_key=key,
        analysis_observation_id=uuid4(),
        baseline_observation_ids=(),
        analysis_bucket_ids=(),
        baseline_start_ns=1,
        baseline_end_ns=2,
        analysis_start_ns=2,
        analysis_end_ns=3,
        latency_deviation_z=latency_z,
        error_excess_z=0.0,
        volume_deviation_z=0.0,
        first_anomalous_bucket_ns=3 if latency_z is not None else None,
        completeness=1.0,
        baseline_bucket_count=6,
        baseline_sample_count=100,
        flags=(),
        formulas={},
        text="computed observation",
    )


def test_high_fanout_truncation_is_stable_and_label_free() -> None:
    symptom = "0" * 64
    neighbor_keys = [f"{index:064x}" for index in range(1, 26)]
    signals = (_signal(symptom, latency_z=4.0), *(_signal(key) for key in neighbor_keys))
    calls = tuple(EligibleCall(symptom, key, (uuid4(),)) for key in neighbor_keys)
    keys, truncated = generate_candidates(signals, calls, symptom)
    assert truncated and len(keys) == 20
    assert keys == (symptom, *neighbor_keys[:19])
    ranked = score_candidates(signals, calls, keys, symptom)
    assert len(ranked) == 20
    assert ranked[0].service_key == symptom
    assert ranked == score_candidates(signals, calls, keys, symptom)


def test_healthy_and_missing_data_templates_are_explicit() -> None:
    healthy = template_narrative("no_anomaly_detected", [], {}, {}, {}, {}, [])
    missing = template_narrative("insufficient_evidence", [], {}, {}, {}, {}, [])
    assert healthy.status == "no_anomaly_detected" and healthy.hypotheses == []
    assert missing.status == "insufficient_evidence" and missing.hypotheses == []
    assert healthy.summary.text != missing.summary.text
