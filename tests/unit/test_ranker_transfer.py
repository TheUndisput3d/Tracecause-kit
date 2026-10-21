"""Transfer features, artifact compatibility and guarded development selection."""

import hashlib
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.fixtures import GROUPS, generate_episode

from tracecause.contracts.models import EpisodeManifest
from tracecause.graph.queries import EligibleCall
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ranking.features import (
    FEATURE_NAMES,
    TRANSFER_FEATURE_NAMES,
    TRANSFER_FEATURE_SCHEMA_VERSION,
    candidate_features,
)
from tracecause.ranking.model import LinearRanker, ModelCompatibilityError
from tracecause.ranking.table import _fact, _ns
from tracecause.ranking.train import fit_feature_table
from tracecause.telemetry.signals import compute_signals

ROOT = Path(__file__).resolve().parents[2]


def test_propagated_symptoms_have_distinct_callee_comparison_and_exact_provenance():
    episode_id, source, raw, _ = generate_episode(GROUPS[0], 0, 20261010)
    manifest = EpisodeManifest.model_validate_json(raw)
    batch = normalize_otlp(
        BytesIO(source),
        encoding=InputEncoding.OTLP_JSONL_V1,
        workspace_id=manifest.workspace_id,
        episode_id=episode_id,
        expected_source_hash=manifest.sources[0].sha256,
        source_name="traces.jsonl",
    )
    facts = tuple(_fact(s, batch.redacted_hash) for s in batch.spans)
    cutoff = _ns(manifest.event_interval.end)
    report = compute_signals(
        facts,
        workspace_id=manifest.workspace_id,
        episode_id=episode_id,
        investigation_start_ns=_ns(manifest.baseline_interval.end),
        as_of_ns=cutoff,
    )
    first = report.signals[0]
    parent = replace(
        first,
        service_key="parent",
        analysis_observation_id=uuid4(),
        error_excess_z=10.0,
        latency_deviation_z=20.0,
    )
    child = replace(
        first,
        service_key="child",
        analysis_observation_id=uuid4(),
        error_excess_z=10.0,
        latency_deviation_z=20.0,
    )
    report = replace(report, signals=(parent, child))
    call_id = uuid4()
    calls = (EligibleCall("parent", "child", (call_id,)),)
    new = candidate_features(
        report,
        calls,
        ("parent", "child"),
        "parent",
        "What changed?",
        version=TRANSFER_FEATURE_SCHEMA_VERSION,
    )
    old = candidate_features(report, calls, ("parent", "child"), "parent", "What changed?")
    assert new[0].values[: len(FEATURE_NAMES)] == old[0].values
    assert new[0].as_map()["error_excess_over_callees"] == 0
    assert new[1].as_map()["error_excess_over_callees"] == 10
    assert new[1].as_map()["positive_error_share"] == 0.5
    required = {
        f"observation:{i}"
        for i in (parent.analysis_observation_id, child.analysis_observation_id, call_id)
    }
    assert required <= set(new[0].evidence_ids)
    assert len(new[0].values) == len(TRANSFER_FEATURE_NAMES)


def test_new_artifact_keeps_legacy_compatible_and_calibrates_public_origin():
    root = ROOT / "models"
    old_raw = (root / "candidate-logistic-v3/model.json").read_bytes()
    assert (
        hashlib.sha256(old_raw).hexdigest()
        == "a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f"
    )
    for version in ("v3", "v4"):
        raw = (root / f"candidate-logistic-{version}/model.json").read_bytes()
        model = LinearRanker.from_bytes(raw, expected_hash=hashlib.sha256(raw).hexdigest())
        if version == "v4":
            assert model.rejection_threshold("captured_demo") != model.rejection_threshold(
                "synthetic"
            )
        assert (
            model.rejection_threshold("unknown")
            == model.artifact["validation"]["rejection"]["threshold"]
        )
    invalid = json.loads(raw)
    invalid["validation"]["rejection_by_origin"]["captured_demo"]["threshold"] = "invalid"
    raw = json.dumps(invalid).encode()
    with pytest.raises(ModelCompatibilityError):
        LinearRanker.from_bytes(raw, expected_hash=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("calibrations", [None, [], "invalid"])
def test_malformed_origin_calibration_is_a_compatibility_error(calibrations):
    model = json.loads((ROOT / "models/candidate-logistic-v4/model.json").read_bytes())
    model["validation"]["rejection_by_origin"] = calibrations
    raw = json.dumps(model).encode()
    with pytest.raises(ModelCompatibilityError):
        LinearRanker.from_bytes(raw, expected_hash=hashlib.sha256(raw).hexdigest())


def test_fit_refuses_held_out_or_cross_split_groups(tmp_path):
    with pytest.raises(ValueError, match="only train/dev"):
        fit_feature_table((), {"episodes": [{"split": "test"}]}, tmp_path)
    with pytest.raises(ValueError, match="crosses"):
        fit_feature_table(
            (),
            {
                "episodes": [
                    {"split": "train", "group_id": "related"},
                    {"split": "dev", "group_id": "related"},
                ]
            },
            tmp_path,
        )
