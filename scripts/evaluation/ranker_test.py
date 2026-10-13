"""One-shot frozen synthetic test ranking diagnostic, separate from training."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from collections import Counter
from pathlib import Path
from typing import Any

from tracecause.evaluation.metrics import paired_episode_bootstrap, score_ranking
from tracecause.ranking.features import CandidateFeatures
from tracecause.ranking.model import LinearRanker
from tracecause.ranking.table import _public_features

from .questions import ROOT

MODEL_HASH = "a824ecaef074ec37402b0caa7bf880c92929e57171b973ff2e0e9e72feec277f"


def evaluate(
    public_root: Path, gold_root: Path, model_file: Path, output_root: Path
) -> dict[str, Any]:
    index_bytes = (gold_root / "dataset.json").read_bytes()
    index = json.loads(index_bytes)
    public_index = json.loads((public_root / "index.json").read_text())
    public_by_id = {item["episode_id"]: item for item in public_index["episodes"]}
    group_splits = {f"g{item['index']:02d}": item["split"] for item in index["group_assignments"]}
    test = [item for item in index["episodes"] if item["split"] == "test"]
    if len(test) != 25 or len(group_splits) != 24:
        raise ValueError("frozen test split count mismatch")
    if len({item["episode_id"] for item in test}) != len(test) or any(
        group_splits[item["group_id"]] != "test" for item in test
    ):
        raise ValueError("test group overlap or duplicate episode")
    model = LinearRanker.from_path(model_file, expected_hash=MODEL_HASH)
    results: list[dict[str, Any]] = []
    for item in sorted(test, key=lambda row: row["episode_id"]):
        episode_id = item["episode_id"]
        public = public_by_id.get(episode_id)
        if public is None:
            raise ValueError("missing public test episode")
        started = time.perf_counter()
        features, source_hash, manifest_hash, name_keys = _public_features(public_root / episode_id)
        if source_hash != public["source_hash"] or manifest_hash != public["manifest_hash"]:
            raise ValueError("public test source integrity failed")
        # Gold is opened only after extraction from public telemetry is complete.
        truth_file = gold_root / episode_id / "truth.json"
        if truth_file.is_symlink():
            raise ValueError("unsafe private truth")
        truth_bytes = truth_file.read_bytes()
        if hashlib.sha256(truth_bytes).hexdigest() != item["truth_hash"]:
            raise ValueError("test truth hash mismatch")
        truth = json.loads(truth_bytes)
        if truth["split"] != "test" or truth["scenario_group"] != item["group_id"]:
            raise ValueError("test truth group mismatch")
        if truth["source_hash"] != source_hash:
            raise ValueError("test truth source mismatch")
        accepted = [
            name_keys.get(name, f"missing:{name}") for name in truth["accepted_target_services"]
        ]
        identifiable = bool(truth["identifiable"])
        learned_scores = {
            row.service_key: model.score(
                CandidateFeatures(row.service_key, row.values, row.evidence_ids)
            )[0]
            for row in features["features"]
        }
        heuristic_scores = features["heuristic"]
        learned = sorted(learned_scores, key=lambda key: (-learned_scores[key], key))
        heuristic = sorted(heuristic_scores, key=lambda key: (-heuristic_scores[key], key))
        learned_metric = score_ranking(learned, accepted, identifiable=identifiable)
        heuristic_metric = score_ranking(heuristic, accepted, identifiable=identifiable)
        results.append(
            {
                "episode_id": episode_id,
                "group_id": item["group_id"],
                "split": "test",
                "origin": "synthetic",
                "source_hash": source_hash,
                "truth_hash": item["truth_hash"],
                "identifiable": identifiable,
                "accepted_target_keys": accepted,
                "candidate_count": len(features["features"]),
                "learned_ranking": learned,
                "heuristic_ranking": heuristic,
                "learned_first_accepted_rank": learned_metric.first_accepted_rank,
                "heuristic_first_accepted_rank": heuristic_metric.first_accepted_rank,
                "learned_hit_at_1": learned_metric.hit_at_1,
                "heuristic_hit_at_1": heuristic_metric.hit_at_1,
                "learned_hit_at_3": learned_metric.hit_at_3,
                "heuristic_hit_at_3": heuristic_metric.hit_at_3,
                "candidate_recalled": learned_metric.candidate_recalled,
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
    eligible = [item for item in results if item["identifiable"]]
    paired = {
        item["episode_id"]: (float(item["learned_hit_at_1"]), float(item["heuristic_hit_at_1"]))
        for item in eligible
    }
    interval = paired_episode_bootstrap(paired) if paired else None
    summary = {
        "schema_version": "1.0",
        "status": "provisional_simulator_truth_not_human_reviewed",
        "origin": "synthetic",
        "split": "test",
        "test_episodes": len(results),
        "identifiable_episodes": len(eligible),
        "healthy_or_unidentifiable": len(results) - len(eligible),
        "candidate_recalled": sum(bool(item["candidate_recalled"]) for item in eligible),
        "learned_hit_at_1": sum(bool(item["learned_hit_at_1"]) for item in eligible),
        "heuristic_hit_at_1": sum(bool(item["heuristic_hit_at_1"]) for item in eligible),
        "learned_hit_at_3": sum(bool(item["learned_hit_at_3"]) for item in eligible),
        "heuristic_hit_at_3": sum(bool(item["heuristic_hit_at_3"]) for item in eligible),
        "paired_hit_at_1_difference": interval.__dict__ if interval else None,
        "model_sha256": MODEL_HASH,
        "private_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "group_counts": dict(Counter(item["group_id"] for item in results)),
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "concurrency": 1,
        },
        "questions_human_reviewed": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "synthetic_test_rankings.json").write_text(
        json.dumps({"summary": summary, "episodes": results}, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=ROOT / "data/generated/seed-20261010")
    parser.add_argument("--gold-root", type=Path, default=ROOT / "data/gold/seed-20261010")
    parser.add_argument(
        "--model-file", type=Path, default=ROOT / "models/candidate-logistic-v3/model.json"
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-provisional")
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.public_root, args.gold_root, args.model_file, args.output_root),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
