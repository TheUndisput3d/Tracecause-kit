"""Grouped, train-only ranker fitting and honest development comparison."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import sklearn  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.model_selection import GroupKFold  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from tracecause.contracts.canonical import canonical_json, semantic_sha256
from tracecause.ranking.model import (
    MODEL_VERSION,
    TRANSFER_MODEL_VERSION,
    LinearRanker,
    export_pipeline,
)
from tracecause.ranking.table import FeatureRow, build_feature_table

SEED = 20261010
REGULARIZATION = (0.01, 0.1, 1.0, 10.0)


def _matrix(rows: tuple[FeatureRow, ...]) -> np.ndarray:
    return np.asarray(
        [[np.nan if value is None else value for value in row.features] for row in rows],
        dtype=np.float64,
    )


def episode_weights(rows: tuple[FeatureRow, ...]) -> np.ndarray:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.episode_id] += 1
    return np.asarray([1.0 / counts[row.episode_id] for row in rows], dtype=np.float64)


def _pipeline(c: float) -> Pipeline:
    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            ),
            ("scaler", StandardScaler()),
            (
                "logistic",
                LogisticRegression(
                    C=c,
                    solver="liblinear",
                    max_iter=1000,
                    random_state=SEED,
                    class_weight=None,
                ),
            ),
        ]
    )


def _fit(rows: tuple[FeatureRow, ...], c: float) -> Pipeline:
    labels = np.asarray([row.label for row in rows], dtype=np.int64)
    if len(set(labels.tolist())) < 2:
        raise ValueError("ranker training fold lacks positive or negative class")
    model = _pipeline(c)
    model.fit(_matrix(rows), labels, logistic__sample_weight=episode_weights(rows))
    return model


def _metrics(
    rows: tuple[FeatureRow, ...],
    scores: np.ndarray,
    *,
    expected_episodes: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    if len(rows) != len(scores):
        raise ValueError("ranking scores and rows differ")
    by_episode: dict[str, list[tuple[FeatureRow, float]]] = defaultdict(list)
    for row, score in zip(rows, scores, strict=True):
        by_episode[row.episode_id].append((row, float(score)))
    if expected_episodes is not None and not set(by_episode) <= {
        item["episode_id"] for item in expected_episodes
    }:
        raise ValueError("ranking rows fall outside declared episodes")
    eligible = [items for items in by_episode.values() if items[0][0].identifiable]
    n = (
        sum(bool(item["identifiable"]) for item in expected_episodes)
        if expected_episodes is not None
        else len(eligible)
    )
    if expected_episodes is not None and n < len(eligible):
        raise ValueError("ranking rows exceed declared identifiable episode set")
    recalled = hit1 = hit3 = 0
    reciprocal = 0.0
    conditional_hit1 = conditional_hit3 = 0
    conditional_reciprocal = 0.0
    for items in eligible:
        ranked = sorted(items, key=lambda item: (-item[1], item[0].candidate_key))
        found = next((i for i, (row, _) in enumerate(ranked, 1) if row.label == 1), None)
        if found is None:
            continue
        recalled += 1
        hit1 += found == 1
        hit3 += found <= 3
        reciprocal += 1.0 / found
        conditional_hit1 += found == 1
        conditional_hit3 += found <= 3
        conditional_reciprocal += 1.0 / found
    return {
        "identifiable_episodes": n,
        "candidate_recalled": recalled,
        "candidate_recall": recalled / n if n else None,
        "hit_at_1": hit1 / n if n else None,
        "hit_at_3": hit3 / n if n else None,
        "mrr": reciprocal / n if n else None,
        "conditional_hit_at_1": conditional_hit1 / recalled if recalled else None,
        "conditional_hit_at_3": conditional_hit3 / recalled if recalled else None,
        "conditional_mrr": conditional_reciprocal / recalled if recalled else None,
        "healthy_or_unidentifiable_episodes": (
            len(expected_episodes) - n if expected_episodes is not None else len(by_episode) - n
        ),
    }


def _rejection_threshold(
    rows: tuple[FeatureRow, ...], scores: np.ndarray, *, total_episodes: int | None = None
) -> dict[str, Any]:
    by_episode: dict[str, list[tuple[FeatureRow, float]]] = defaultdict(list)
    for row, score in zip(rows, scores, strict=True):
        by_episode[row.episode_id].append((row, float(score)))
    top = [
        max(items, key=lambda item: (item[1], item[0].candidate_key))
        for items in by_episode.values()
        if items
    ]
    if not top:
        return {"threshold": None, "answered": 0, "correct": 0, "false_alarm": 0}
    candidates = sorted({item[1] for item in top})
    candidates = [candidates[0] - 1.0, *candidates, candidates[-1] + 1.0]
    best: tuple[float, float, int, int, int] | None = None
    for threshold in candidates:
        answered = [item for item in top if item[1] >= threshold]
        correct = sum(row.label == 1 for row, _ in answered if row.identifiable)
        incorrect = sum(row.identifiable and row.label == 0 for row, _ in answered)
        false_alarm = sum(not row.identifiable for row, _ in answered)
        objective = float(correct - incorrect - false_alarm)
        current = (objective, threshold, len(answered), correct, false_alarm)
        if best is None or (current[0], current[1]) > (best[0], best[1]):
            best = current
    assert best is not None
    return {
        "threshold": best[1],
        "answered": best[2],
        "correct": best[3],
        "false_alarm": best[4],
        "objective": best[0],
        "coverage": best[2] / (total_episodes or len(top)),
        "accuracy_among_answered": best[3] / best[2] if best[2] else None,
    }


def train_and_report(public_root: Path, gold_root: Path, output_root: Path) -> dict[str, Any]:
    rows, table_metadata = build_feature_table(public_root, gold_root)
    return fit_feature_table(rows, table_metadata, output_root)


def fit_feature_table(
    rows: tuple[FeatureRow, ...],
    table_metadata: dict[str, Any],
    output_root: Path,
    *,
    model_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    """Fit declared train rows only; preserve the historical v3 training entry point."""
    if any(row.split not in {"train", "dev"} for row in rows) or any(
        item["split"] not in {"train", "dev"} for item in table_metadata["episodes"]
    ):
        raise ValueError("training table may contain only train/dev rows")
    group_splits: dict[str, set[str]] = defaultdict(set)
    for item in table_metadata["episodes"]:
        group_splits[item["group_id"]].add(item["split"])
    if any(len(splits) != 1 for splits in group_splits.values()):
        raise ValueError("scenario group crosses training/development boundary")
    train = tuple(row for row in rows if row.split == "train" and row.identifiable)
    dev = tuple(row for row in rows if row.split == "dev")
    if not train or not dev:
        raise ValueError("training or development feature table empty")
    unique_groups = sorted(
        {
            item["group_id"]
            for item in table_metadata["episodes"]
            if item["split"] == "train" and item["identifiable"]
        }
    )
    folds = min(5, len(unique_groups))
    if folds < 2:
        raise ValueError("too few identifiable training groups")
    cv = GroupKFold(n_splits=folds)
    candidate_cv: list[dict[str, Any]] = []
    for c in REGULARIZATION:
        fold_results: list[dict[str, Any]] = []
        for fit_idx, val_idx in cv.split(unique_groups, groups=unique_groups):
            fit_groups = {unique_groups[i] for i in fit_idx}
            validation_groups = {unique_groups[i] for i in val_idx}
            fit_rows = tuple(row for row in train if row.group_id in fit_groups)
            val_rows = tuple(row for row in train if row.group_id in validation_groups)
            model = _fit(fit_rows, c)
            prediction = (
                model.decision_function(_matrix(val_rows))
                if val_rows
                else np.asarray([], dtype=np.float64)
            )
            fold_episodes = tuple(
                item
                for item in table_metadata["episodes"]
                if item["split"] == "train" and item["group_id"] in validation_groups
            )
            fold_results.append(
                {
                    "fit_groups": sorted(fit_groups),
                    "validation_groups": sorted(validation_groups),
                    "metrics": _metrics(val_rows, prediction, expected_episodes=fold_episodes),
                }
            )
        valid_mrr = [
            item["metrics"]["mrr"] for item in fold_results if item["metrics"]["mrr"] is not None
        ]
        candidate_cv.append(
            {
                "c": c,
                "mean_mrr": float(np.mean(valid_mrr)) if valid_mrr else None,
                "folds": fold_results,
            }
        )
    dev_episodes = tuple(item for item in table_metadata["episodes"] if item["split"] == "dev")
    by_origin = {
        origin: tuple(e for e in dev_episodes if e.get("origin") == origin)
        for origin in ("synthetic", "captured_demo")
    }
    if model_version == TRANSFER_MODEL_VERSION:
        if any(not episodes for episodes in by_origin.values()):
            raise ValueError("transfer selection requires both development origins")
        for candidate in candidate_cv:
            fitted = _fit(train, candidate["c"])
            origin_metrics = {}
            for origin, episodes in by_origin.items():
                ids = {e["episode_id"] for e in episodes}
                subset = tuple(row for row in dev if row.episode_id in ids)
                origin_metrics[origin] = _metrics(
                    subset, fitted.decision_function(_matrix(subset)), expected_episodes=episodes
                )
            candidate["development_by_origin"] = origin_metrics
            candidate["minimum_origin_hit_at_1"] = min(
                metric["hit_at_1"] or 0 for metric in origin_metrics.values()
            )
            candidate["mean_origin_mrr"] = float(
                np.mean([metric["mrr"] or 0 for metric in origin_metrics.values()])
            )
        chosen = max(
            candidate_cv,
            key=lambda item: (
                item["minimum_origin_hit_at_1"],
                item["mean_origin_mrr"],
                item["mean_mrr"] or -1.0,
                -item["c"],
            ),
        )
    else:
        chosen = max(candidate_cv, key=lambda item: (item["mean_mrr"] or -1.0, -item["c"]))
    model = _fit(train, chosen["c"])
    dev_scores = model.decision_function(_matrix(dev))
    heuristic_scores = np.asarray([row.heuristic_score for row in dev], dtype=np.float64)
    dev_learned = _metrics(dev, dev_scores, expected_episodes=dev_episodes)
    dev_heuristic = _metrics(dev, heuristic_scores, expected_episodes=dev_episodes)
    threshold = _rejection_threshold(dev, dev_scores, total_episodes=len(dev_episodes))
    selected_default = (
        "learned"
        if (dev_learned["hit_at_1"] or 0) > (dev_heuristic["hit_at_1"] or 0)
        else "heuristic"
    )
    training_metadata = {
        "seed": SEED,
        "selected_c": chosen["c"],
        "training_groups": unique_groups,
        "training_episode_count": len({row.episode_id for row in train}),
        "training_row_count": len(train),
        "training_rows_hash": semantic_sha256({"rows": [asdict(row) for row in train]}),
        "table_rows_hash": table_metadata["rows_hash"],
        "private_index_hash": table_metadata["private_index_hash"],
        "class_weight": None,
        "sample_weight": "each eligible episode's candidate rows sum to 1",
        "healthy_unidentifiable": "excluded from relevance fit; included in rejection evaluation",
        "group_cross_validation_folds": folds,
    }
    validation: dict[str, Any] = {
        "development_learned": dev_learned,
        "development_heuristic": dev_heuristic,
        "rejection": threshold,
        "selected_default": selected_default,
    }
    if model_version == TRANSFER_MODEL_VERSION:
        training_metadata["selection_criterion"] = (
            "development minimum-origin Hit@1, then mean-origin MRR, then grouped train CV MRR"
        )
        training_metadata["origin_counts"] = {
            origin: sum(
                e.get("origin") == origin and e["split"] == "train" and e["identifiable"]
                for e in table_metadata["episodes"]
            )
            for origin in by_origin
        }
        validation["rejection_by_origin"] = {}
        for origin, episodes in by_origin.items():
            ids = {e["episode_id"] for e in episodes}
            subset = tuple(row for row in dev if row.episode_id in ids)
            validation["rejection_by_origin"][origin] = _rejection_threshold(
                subset, model.decision_function(_matrix(subset)), total_episodes=len(episodes)
            )
        validation["development_by_origin"] = chosen["development_by_origin"]
    raw = export_pipeline(model, training_metadata, validation, model_version=model_version)
    digest = hashlib.sha256(raw).hexdigest()
    ranker = LinearRanker.from_bytes(raw, expected_hash=digest)
    for row, sklearn_score in zip(dev, dev_scores, strict=True):
        from tracecause.ranking.features import CandidateFeatures

        exported_score, _ = ranker.score(
            CandidateFeatures(row.candidate_key, row.features, row.evidence_ids)
        )
        if abs(exported_score - sklearn_score) > 1e-8:
            raise ValueError("exported model differs from scikit-learn pipeline")
    report = {
        "status": "development_only",
        "artifact_sha256": digest,
        "table": table_metadata,
        "train_identifiable_episodes": len({row.episode_id for row in train}),
        "cv": candidate_cv,
        "validation": validation,
        "library_versions": {"numpy": np.__version__, "scikit_learn": sklearn.__version__},
        "test_labels_opened": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "feature_table.jsonl").write_bytes(
        b"".join(canonical_json(asdict(row)) + b"\n" for row in rows)
    )
    (output_root / "model.json").write_bytes(raw)
    (output_root / "development_report.json").write_bytes(canonical_json(report) + b"\n")
    return report
