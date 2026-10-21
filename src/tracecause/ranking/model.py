"""Auditable JSON export and inference for a grouped linear candidate scorer."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sklearn  # type: ignore[import-untyped]

from tracecause.contracts.canonical import canonical_json
from tracecause.ranking.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    TRANSFER_FEATURE_NAMES,
    TRANSFER_FEATURE_SCHEMA_VERSION,
    CandidateFeatures,
)

MODEL_VERSION = "candidate-logistic-v3"
TRANSFER_MODEL_VERSION = "candidate-logistic-v4"
SUPPORTED_MODEL_VERSIONS = (MODEL_VERSION, TRANSFER_MODEL_VERSION)


class ModelCompatibilityError(ValueError):
    """Artifact is untrusted, invalid or incompatible with production features."""


@dataclass(frozen=True)
class LinearRanker:
    artifact: dict[str, Any]
    artifact_hash: str

    @classmethod
    def from_bytes(cls, raw: bytes, *, expected_hash: str) -> LinearRanker:
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ModelCompatibilityError("ranker artifact hash mismatch")
        try:
            payload = json.loads(raw)
            feature_names = (
                TRANSFER_FEATURE_NAMES
                if payload["model_version"] == TRANSFER_MODEL_VERSION
                else FEATURE_NAMES
            )
            feature_schema = (
                TRANSFER_FEATURE_SCHEMA_VERSION
                if payload["model_version"] == TRANSFER_MODEL_VERSION
                else FEATURE_SCHEMA_VERSION
            )
            if (
                payload["model_version"] not in SUPPORTED_MODEL_VERSIONS
                or payload["feature_schema_version"] != feature_schema
                or tuple(payload["raw_feature_names"]) != feature_names
                or payload["model_type"] != "imputer_scaler_logistic"
            ):
                raise ModelCompatibilityError("ranker feature or model version mismatch")
            n_raw = len(feature_names)
            indicators = payload["missing_indicator_positions"]
            n_transformed = n_raw + len(indicators)
            if (
                len(payload["imputation_values"]) != n_raw
                or len(payload["scaler_means"]) != n_transformed
                or len(payload["scaler_scales"]) != n_transformed
                or len(payload["coefficients"]) != n_transformed
                or any(type(pos) is not int or pos < 0 or pos >= n_raw for pos in indicators)
                or len(set(indicators)) != len(indicators)
            ):
                raise ModelCompatibilityError("ranker parameter dimensions invalid")
            numeric = [
                *payload["imputation_values"],
                *payload["scaler_means"],
                *payload["scaler_scales"],
                *payload["coefficients"],
                payload["intercept"],
            ]
            if any(
                type(value) not in {int, float} or not math.isfinite(value) for value in numeric
            ):
                raise ModelCompatibilityError("ranker parameters are not finite")
            if any(scale <= 0 for scale in payload["scaler_scales"]):
                raise ModelCompatibilityError("ranker scale invalid")
            threshold = payload["validation"]["rejection"]["threshold"]
            if threshold is not None and (
                type(threshold) not in {int, float} or not math.isfinite(threshold)
            ):
                raise ModelCompatibilityError("ranker rejection threshold invalid")
            calibrations = payload["validation"].get("rejection_by_origin", {})
            if not isinstance(calibrations, dict):
                raise ModelCompatibilityError("ranker origin calibration must be an object")
            for origin, calibration in calibrations.items():
                cutoff = calibration["threshold"]
                if origin not in {"synthetic", "captured_demo"} or (
                    cutoff is not None
                    and (type(cutoff) not in {int, float} or not math.isfinite(cutoff))
                ):
                    raise ModelCompatibilityError("ranker origin calibration invalid")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ModelCompatibilityError("ranker artifact invalid") from exc
        return cls(payload, expected_hash)

    @classmethod
    def from_path(cls, path: Path, *, expected_hash: str) -> LinearRanker:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ModelCompatibilityError("ranker artifact path invalid")
        return cls.from_bytes(path.read_bytes(), expected_hash=expected_hash)

    def score(self, features: CandidateFeatures) -> tuple[float, tuple[dict[str, Any], ...]]:
        raw = features.values
        feature_names = tuple(self.artifact["raw_feature_names"])
        if len(raw) != len(feature_names):
            raise ModelCompatibilityError("ranker feature vector length mismatch")
        artifact = self.artifact
        imputed = [
            artifact["imputation_values"][i] if value is None else value
            for i, value in enumerate(raw)
        ]
        indicators = [
            float(raw[position] is None) for position in artifact["missing_indicator_positions"]
        ]
        transformed = [*imputed, *indicators]
        standardized = [
            (value - mean) / scale
            for value, mean, scale in zip(
                transformed,
                artifact["scaler_means"],
                artifact["scaler_scales"],
                strict=True,
            )
        ]
        contributions = [
            value * coefficient
            for value, coefficient in zip(standardized, artifact["coefficients"], strict=True)
        ]
        value = float(artifact["intercept"] + sum(contributions))
        if not math.isfinite(value):
            raise ModelCompatibilityError("ranker produced nonfinite score")
        names = [
            *feature_names,
            *(
                f"missing:{feature_names[position]}"
                for position in artifact["missing_indicator_positions"]
            ),
        ]
        explanation = tuple(
            {
                "feature": name,
                "standardized_contribution": contribution,
                "evidence_ids": features.evidence_ids,
            }
            for name, contribution in zip(names, contributions, strict=True)
        )
        return value, explanation

    def rejection_threshold(self, origin: str) -> float | None:
        validation = self.artifact["validation"]
        threshold = validation.get("rejection_by_origin", {}).get(origin, validation["rejection"])[
            "threshold"
        ]
        return float(threshold) if threshold is not None else None


def export_pipeline(
    pipeline: Any,
    metadata: dict[str, Any],
    validation: dict[str, Any],
    *,
    model_version: str = MODEL_VERSION,
) -> bytes:
    if model_version not in SUPPORTED_MODEL_VERSIONS:
        raise ModelCompatibilityError("unsupported export version")
    feature_names = (
        TRANSFER_FEATURE_NAMES if model_version == TRANSFER_MODEL_VERSION else FEATURE_NAMES
    )
    feature_schema = (
        TRANSFER_FEATURE_SCHEMA_VERSION
        if model_version == TRANSFER_MODEL_VERSION
        else FEATURE_SCHEMA_VERSION
    )
    imputer = pipeline.named_steps["imputer"]
    scaler = pipeline.named_steps["scaler"]
    logistic = pipeline.named_steps["logistic"]
    indicator = imputer.indicator_.features_.tolist()
    payload = {
        "model_version": model_version,
        "model_type": "imputer_scaler_logistic",
        "feature_schema_version": feature_schema,
        "raw_feature_names": feature_names,
        "transformed_feature_names": [
            *feature_names,
            *(f"missing:{feature_names[position]}" for position in indicator),
        ],
        "imputation_values": imputer.statistics_.astype(float).tolist(),
        "missing_indicator_positions": indicator,
        "scaler_means": scaler.mean_.astype(float).tolist(),
        "scaler_scales": scaler.scale_.astype(float).tolist(),
        "coefficients": logistic.coef_[0].astype(float).tolist(),
        "intercept": float(logistic.intercept_[0]),
        "training": metadata,
        "validation": validation,
        "library_versions": {
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "python": platform.python_version(),
        },
    }
    return canonical_json(payload) + b"\n"
