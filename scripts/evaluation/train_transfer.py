"""Train a new version on synthetic plus reviewed captured train groups only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from tracecause.contracts.canonical import canonical_json, semantic_sha256
from tracecause.investigation.ranking import generate_candidates, score_candidates
from tracecause.ranking.features import (
    FEATURE_NAMES,
    TRANSFER_FEATURE_SCHEMA_VERSION,
    CandidateFeatures,
    candidate_features,
)
from tracecause.ranking.model import TRANSFER_MODEL_VERSION, LinearRanker
from tracecause.ranking.table import CANONICAL_QUESTION, FeatureRow, build_feature_table
from tracecause.ranking.train import _metrics, fit_feature_table

from .systems import load_episode


def captured_rows(root: Path) -> tuple[tuple[FeatureRow, ...], dict]:
    prepared = root / "reports/phase13-captured-prepared-v1"
    index = json.loads((prepared / "index.json").read_bytes())
    hashes = {row["episode_id"]: row["sha256"] for row in index["episodes"]}
    validation = json.loads((root / "reports/capture/ai_review_validation.json").read_bytes())
    sheet = root / "reports/capture/ai_review_sheet.csv"
    if hashlib.sha256(sheet.read_bytes()).hexdigest() != validation["review_sha256"]:
        raise ValueError("captured review sheet changed since validation")
    schedule = json.loads((root / "configs/capture_schedule.json").read_bytes())
    allowed = {r["key"]: r for r in schedule["runs"] if r["split"] in {"train", "dev"}}
    rows, episodes, seen = [], [], set()
    with sheet.open(newline="") as source:
        for row in csv.DictReader(source):
            if row["key"] not in allowed:
                continue  # Do not load test evidence or parse its target judgments.
            spec = allowed[row["key"]]
            if row["split"] != spec["split"] or row["key"] in seen:
                raise ValueError("captured review split or key differs")
            seen.add(row["key"])
            if spec["mode"] == "healthy" and row["split"] == "train":
                # No invented relevance labels; these controls cannot enter the fit.
                continue
            identifier = row["episode_id"]
            episode = load_episode(prepared / f"{identifier}.json", hashes[identifier])
            symptom = episode.names.get("storefront")
            keys, _ = generate_candidates(episode.report.signals, episode.calls, symptom)
            features = candidate_features(
                episode.report,
                episode.calls,
                keys,
                symptom,
                CANONICAL_QUESTION,
                version=TRANSFER_FEATURE_SCHEMA_VERSION,
            )
            heuristic = {
                score.service_key: score.score
                for score in score_candidates(episode.report.signals, episode.calls, keys, symptom)
            }
            identifiable = row["identifiable"] == "yes"
            accepted = {
                episode.names[name.strip()]
                for name in row["accepted_targets"].split(";")
                if name.strip()
            }
            if identifiable and not accepted:
                raise ValueError("identifiable capture lacks reviewed target")
            # All repeated captures of a fault family stay in one validation fold.
            group = "captured:" + hashlib.sha256(spec["mode"].encode()).hexdigest()[:12]
            found = bool(accepted & set(keys)) if identifiable else False
            episodes.append(
                {
                    "episode_id": identifier,
                    "group_id": group,
                    "split": row["split"],
                    "identifiable": identifiable,
                    "target_in_candidates": found,
                    "candidate_count": len(features),
                    "prepared_hash": hashes[identifier],
                }
            )
            for feature in features:
                rows.append(
                    FeatureRow(
                        identifier,
                        group,
                        row["split"],
                        episode.cutoff,
                        feature.service_key,
                        feature.values,
                        feature.evidence_ids,
                        heuristic[feature.service_key],
                        int(feature.service_key in accepted) if identifiable else None,
                        identifiable,
                        found,
                        hashes[identifier],
                        episode.report.input_hash,
                    )
                )
    if seen != set(allowed):
        raise ValueError("captured training/development review is incomplete")
    return tuple(rows), {"episodes": episodes, "review_hash": validation["review_sha256"]}


def run(root: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("transfer experiment needs a new output directory")
    synthetic, meta = build_feature_table(
        root / "data/generated/seed-20261010",
        root / "data/gold/seed-20261010",
        feature_schema=TRANSFER_FEATURE_SCHEMA_VERSION,
    )
    captures, capture_meta = captured_rows(root)
    rows = (*synthetic, *captures)
    episodes = [
        *({**e, "origin": "synthetic"} for e in meta["episodes"]),
        *({**e, "origin": "captured_demo"} for e in capture_meta["episodes"]),
    ]
    metadata = {
        **meta,
        "episodes": episodes,
        "rows_hash": semantic_sha256({"rows": [asdict(row) for row in rows]}),
        "private_index_hash": semantic_sha256(
            {
                "synthetic_access": meta["private_index_hash"],
                "captured_review": capture_meta["review_hash"],
                "allowed_splits": ["train", "dev"],
            }
        ),
        "captured_test_evidence_opened": False,
    }
    report = fit_feature_table(rows, metadata, output, model_version=TRANSFER_MODEL_VERSION)
    raw = (output / "model.json").read_bytes()
    new = LinearRanker.from_bytes(raw, expected_hash=hashlib.sha256(raw).hexdigest())
    old_raw = (root / "models/candidate-logistic-v3/model.json").read_bytes()
    old = LinearRanker.from_bytes(old_raw, expected_hash=hashlib.sha256(old_raw).hexdigest())
    comparisons = {}
    for origin, subset in (("synthetic", synthetic), ("captured_demo", captures)):
        dev = tuple(row for row in subset if row.split == "dev")
        ids = {row.episode_id for row in subset}
        expected = tuple(e for e in episodes if e["split"] == "dev" and e["episode_id"] in ids)
        comparisons[origin] = {}
        for name, model in (("old_v3", old), ("new_v4", new), ("heuristic", None)):
            scores = np.asarray(
                [
                    model.score(
                        CandidateFeatures(
                            r.candidate_key,
                            r.features[: len(FEATURE_NAMES)] if name == "old_v3" else r.features,
                            r.evidence_ids,
                        )
                    )[0]
                    if model is not None
                    else r.heuristic_score
                    for r in dev
                ]
            )
            metrics = _metrics(dev, scores, expected_episodes=expected)
            if model is not None:
                threshold = model.rejection_threshold(origin)
                top = {}
                for row, score in zip(dev, scores, strict=True):
                    prior = top.get(row.episode_id)
                    if prior is None or score > prior[1]:
                        top[row.episode_id] = (row, float(score))
                answered = [r for r, s in top.values() if threshold is not None and s >= threshold]
                metrics["threshold_answered"] = len(answered)
                metrics["threshold_correct"] = sum(
                    r.identifiable and r.label == 1 for r in answered
                )
                metrics["threshold_false_alarm"] = sum(not r.identifiable for r in answered)
            comparisons[origin][name] = metrics
    summary = {
        "status": "development_experiment_not_held_out_test",
        "model_sha256": report["artifact_sha256"],
        "training_episodes": report["train_identifiable_episodes"],
        "training_rows": new.artifact["training"]["training_row_count"],
        "test_used_for_fit_or_selection": False,
        "review_origin": "ai",
        "independent_human_review": False,
        "comparisons": comparisons,
    }
    (output / "transfer_report.json").write_bytes(canonical_json(summary) + b"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path.cwd(), args.output), indent=2))


if __name__ == "__main__":
    main()
