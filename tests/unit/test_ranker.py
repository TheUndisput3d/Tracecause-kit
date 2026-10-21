"""Ranker artifact, weights, split and future-data regression tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from scripts.fixtures import GROUPS, generate_episode

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ranking.features import FEATURE_NAMES, CandidateFeatures, candidate_features
from tracecause.ranking.model import LinearRanker, ModelCompatibilityError, export_pipeline
from tracecause.ranking.table import CANONICAL_QUESTION, FeatureRow, _fact, _ns, build_feature_table
from tracecause.ranking.train import _fit, _matrix, _metrics, episode_weights, train_and_report
from tracecause.telemetry.signals import compute_signals


def _row(episode: str, group: str, candidate: str, label: int | None) -> FeatureRow:
    values = tuple(float(i + 1) if i % 3 else None for i in range(len(FEATURE_NAMES)))
    return FeatureRow(
        episode_id=episode,
        group_id=group,
        split="train",
        cutoff_ns=1767226200000000000,
        candidate_key=candidate,
        features=values,
        evidence_ids=("observation:00000000-0000-4000-8000-000000000001",),
        heuristic_score=0.0,
        label=label,
        identifiable=label is not None,
        target_in_candidates=label == 1,
        source_hash="a" * 64,
        signal_input_hash="b" * 64,
    )


def test_episode_weights_and_missing_candidate_failure() -> None:
    rows = (
        _row("one", "g01", "a", 1),
        _row("one", "g01", "b", 0),
        _row("two", "g02", "c", 0),
        _row("three", "g03", "d", None),
    )
    assert episode_weights(rows).tolist() == [0.5, 0.5, 1.0, 1.0]
    metrics = _metrics(rows, np.asarray([2.0, 1.0, 3.0, 0.0]))
    assert metrics["identifiable_episodes"] == 2
    assert metrics["candidate_recalled"] == 1
    assert metrics["candidate_recall"] == 0.5
    assert metrics["hit_at_1"] == 0.5
    missing_row_metrics = _metrics(
        rows[:2],
        np.asarray([2.0, 1.0]),
        expected_episodes=(
            {"episode_id": "one", "identifiable": True},
            {"episode_id": "missing", "identifiable": True},
        ),
    )
    assert missing_row_metrics["identifiable_episodes"] == 2
    assert missing_row_metrics["candidate_recall"] == 0.5


def test_all_negative_fold_rejected() -> None:
    with pytest.raises(ValueError, match="lacks positive"):
        _fit((_row("one", "g01", "a", 0), _row("two", "g02", "b", 0)), 0.1)


def test_exported_linear_scorer_matches_sklearn_and_rejects_tampering() -> None:
    rows = (
        _row("one", "g01", "a", 1),
        replace(
            _row("one", "g01", "b", 0), features=tuple(float(i) for i in range(len(FEATURE_NAMES)))
        ),
        replace(
            _row("two", "g02", "c", 0),
            features=tuple(float(i + 2) for i in range(len(FEATURE_NAMES))),
        ),
    )
    pipeline = _fit(rows, 0.1)
    raw = export_pipeline(
        pipeline,
        {"rows": 3},
        {"selected_default": "learned", "rejection": {"threshold": 0.0}},
    )
    digest = hashlib.sha256(raw).hexdigest()
    ranker = LinearRanker.from_bytes(raw, expected_hash=digest)
    for row, expected in zip(rows, pipeline.decision_function(_matrix(rows)), strict=True):
        observed, contributions = ranker.score(
            CandidateFeatures(row.candidate_key, row.features, row.evidence_ids)
        )
        assert abs(observed - expected) < 1e-9
        assert contributions and contributions[0]["evidence_ids"] == row.evidence_ids
    with pytest.raises(ModelCompatibilityError, match="hash mismatch"):
        LinearRanker.from_bytes(raw, expected_hash="0" * 64)
    altered = json.loads(raw)
    altered["raw_feature_names"] = list(reversed(altered["raw_feature_names"]))
    corrupt = json.dumps(altered).encode()
    with pytest.raises(ModelCompatibilityError, match="invalid"):
        LinearRanker.from_bytes(corrupt, expected_hash=hashlib.sha256(corrupt).hexdigest())
    with pytest.raises(ModelCompatibilityError, match="length mismatch"):
        ranker.score(CandidateFeatures("a", (1.0,), ()))


def test_future_span_and_gold_metadata_do_not_change_features_or_prediction() -> None:
    episode_id, source, manifest_raw, truth_raw = generate_episode(GROUPS[0], 0, 20261010)
    manifest = EpisodeManifest.model_validate_json(manifest_raw)
    batch = normalize_otlp(
        BytesIO(source),
        encoding=InputEncoding.OTLP_JSONL_V1,
        workspace_id=manifest.workspace_id,
        episode_id=episode_id,
        expected_source_hash=manifest.sources[0].sha256,
        source_name="traces.jsonl",
    )
    assert not batch.fatal and not batch.rejected_count
    facts = tuple(_fact(item, batch.redacted_hash) for item in batch.spans)
    cutoff = _ns(manifest.event_interval.end)
    start = _ns(manifest.baseline_interval.end) if manifest.baseline_interval else 0
    report = compute_signals(
        facts,
        workspace_id=manifest.workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    future = replace(
        facts[0],
        start_ns=cutoff + 10_000_000_000,
        end_ns=cutoff + 11_000_000_000,
        normalized_hash="f" * 64,
    )
    altered_report = compute_signals(
        (*facts, future),
        workspace_id=manifest.workspace_id,
        episode_id=episode_id,
        investigation_start_ns=start,
        as_of_ns=cutoff,
    )
    truth = json.loads(truth_raw)
    truth["target_service"] = "invented-future-target"
    assert report.input_hash == altered_report.input_hash
    keys = tuple(item.service_key for item in report.signals)
    original = candidate_features(report, (), keys, None, CANONICAL_QUESTION)
    changed = candidate_features(altered_report, (), keys, None, CANONICAL_QUESTION)
    assert original == changed
    assert truth["target_service"] not in repr(original)


def test_training_reads_only_declared_train_dev_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tracecause.ranking import table

    public_root = tmp_path / "public"
    gold_root = tmp_path / "gold"
    gold_root.mkdir()
    episode_id = str(uuid4())
    test_id = str(uuid4())
    truth = {
        "split": "train",
        "scenario_group": "g00",
        "identifiable": True,
        "accepted_target_services": ["payment"],
    }
    truth_bytes = json.dumps(truth).encode()
    truth_file = gold_root / episode_id / "truth.json"
    truth_file.parent.mkdir()
    truth_file.write_bytes(truth_bytes)
    (gold_root / "dataset.json").write_text("THIS FILE MUST NOT BE READ")
    index = {
        "schema_version": "1.0",
        "groups": [{"group_id": "g00", "split": "train"}],
        "episodes": [
            {
                "episode_id": episode_id,
                "group_id": "g00",
                "split": "train",
                "truth_hash": hashlib.sha256(truth_bytes).hexdigest(),
            }
        ],
    }
    (gold_root / "training_access.json").write_text(json.dumps(index))

    def fake_public(path: Path, feature_schema=table.FEATURE_SCHEMA_VERSION):
        assert path == public_root / episode_id
        return (
            {
                "episode_id": episode_id,
                "cutoff_ns": 123,
                "features": (CandidateFeatures("key1", (1.0,) * len(FEATURE_NAMES), ()),),
                "heuristic": {"key1": 0.5},
                "signal_input_hash": "b" * 64,
            },
            "a" * 64,
            "c" * 64,
            {"payment": "key1"},
        )

    monkeypatch.setattr(table, "_public_features", fake_public)
    rows, _ = build_feature_table(public_root, gold_root, splits=("train",))
    assert len(rows) == 1 and rows[0].label == 1
    with pytest.raises(ValueError, match="only train/dev"):
        build_feature_table(public_root, gold_root, splits=("test",))
    index["episodes"].append(
        {"episode_id": test_id, "group_id": "g23", "split": "test", "truth_hash": "0" * 64}
    )
    (gold_root / "training_access.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="held-out"):
        build_feature_table(public_root, gold_root)


def test_small_group_folds_and_training_reproducibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tracecause.ranking import train

    rows: list[FeatureRow] = []
    for index in range(4):
        split = "train" if index < 3 else "dev"
        for label in (1, 0):
            feature_values = tuple(
                float(label * 3 + index / 10 + position / 100)
                for position in range(len(FEATURE_NAMES))
            )
            rows.append(
                replace(
                    _row(f"episode-{index}", f"g{index:02d}", f"service-{label}", label),
                    split=split,
                    features=feature_values,
                )
            )
    metadata = {
        "rows_hash": "a" * 64,
        "private_index_hash": "b" * 64,
        "episodes": [
            {
                "episode_id": f"episode-{index}",
                "group_id": f"g{index:02d}",
                "split": "train" if index < 3 else "dev",
                "identifiable": True,
            }
            for index in range(4)
        ]
        + [
            {
                "episode_id": "episode-with-no-candidates",
                "group_id": "g04",
                "split": "train",
                "identifiable": True,
            }
        ],
    }
    monkeypatch.setattr(train, "build_feature_table", lambda *_args: (tuple(rows), metadata))
    first = train_and_report(tmp_path / "public", tmp_path / "gold", tmp_path / "first")
    second = train_and_report(tmp_path / "public", tmp_path / "gold", tmp_path / "second")
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert len(first["cv"][0]["folds"]) == 4
    assert any(fold["metrics"]["candidate_recall"] == 0.0 for fold in first["cv"][0]["folds"])
    assert (tmp_path / "first/model.json").read_bytes() == (
        tmp_path / "second/model.json"
    ).read_bytes()
