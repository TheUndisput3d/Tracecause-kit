"""Offline feature rows from public traces; private labels enter only after extraction."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from tracecause.contracts.canonical import bytes_sha256, semantic_sha256
from tracecause.contracts.models import EpisodeManifest, NormalizedSpan
from tracecause.graph.queries import EligibleCall
from tracecause.ingestion.normalize import normalize_otlp, normalized_span_semantic_hash
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.investigation.ranking import generate_candidates, score_candidates
from tracecause.ranking.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    TRANSFER_FEATURE_NAMES,
    TRANSFER_FEATURE_SCHEMA_VERSION,
    candidate_features,
)
from tracecause.telemetry.signals import SpanFact, compute_signals

CANONICAL_QUESTION = "Which service should be checked first?"


@dataclass(frozen=True)
class FeatureRow:
    episode_id: str
    group_id: str
    split: str
    cutoff_ns: int
    candidate_key: str
    features: tuple[float | None, ...]
    evidence_ids: tuple[str, ...]
    heuristic_score: float
    label: int | None
    identifiable: bool
    target_in_candidates: bool
    source_hash: str
    signal_input_hash: str


def _ns(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def _fact(span: NormalizedSpan, artifact_hash: str) -> SpanFact:
    return SpanFact(
        workspace_id=span.workspace_id,
        episode_id=span.episode_id,
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        service_key=span.service_key,
        kind=span.kind,
        status=span.status,
        start_ns=int(span.start_ns),
        end_ns=int(span.end_ns),
        available_at=span.available_at,
        safe_attributes=span.safe_attributes,
        quality_flags=tuple(span.quality_flags),
        normalized_hash=normalized_span_semantic_hash(span),
        artifact_hash=artifact_hash,
        source_record_offset=span.source_record_offset,
        source_line_number=span.source_line_number,
        source_resource_index=span.source_resource_index,
        source_scope_index=span.source_scope_index,
        source_span_index=span.source_span_index,
    )


def _public_features(
    episode_dir: Path, feature_schema: str = FEATURE_SCHEMA_VERSION
) -> tuple[dict[str, Any], str, str, dict[str, str]]:
    manifest_file = episode_dir / "manifest.json"
    source_file = episode_dir / "traces.jsonl"
    if episode_dir.is_symlink() or manifest_file.is_symlink() or source_file.is_symlink():
        raise ValueError("unsafe public episode path")
    manifest = EpisodeManifest.model_validate_json(manifest_file.read_bytes())
    if manifest.baseline_interval is None:
        raise ValueError("public episode lacks baseline interval")
    source = manifest.sources[0]
    if source.kind != "otlp_jsonl" or source.logical_name != "traces.jsonl":
        raise ValueError("unexpected public source")
    with source_file.open("rb") as stream:
        batch = normalize_otlp(
            stream,
            encoding=InputEncoding.OTLP_JSONL_V1,
            workspace_id=manifest.workspace_id,
            episode_id=manifest.episode_id,
            expected_source_hash=source.sha256,
            source_name=source.logical_name,
        )
    if batch.fatal or batch.rejected_count or batch.source_hash != source.sha256:
        raise ValueError("public source failed normalization")
    facts = tuple(_fact(span, batch.redacted_hash) for span in batch.spans)
    start_ns = _ns(manifest.baseline_interval.end)
    cutoff_ns = _ns(manifest.event_interval.end)
    report = compute_signals(
        facts,
        workspace_id=manifest.workspace_id,
        episode_id=manifest.episode_id,
        investigation_start_ns=start_ns,
        as_of_ns=cutoff_ns,
        mode="event",
    )
    call_ids: dict[tuple[str, str], set[UUID]] = {}
    for observation in report.observations:
        if (
            observation.observation_type == "dependency"
            and observation.sample_count > 0
            and observation.caller_service_key
            and observation.callee_service_key
        ):
            pair = (observation.caller_service_key, observation.callee_service_key)
            call_ids.setdefault(pair, set()).add(observation.id)
    calls = tuple(
        EligibleCall(caller, callee, tuple(sorted(ids, key=str)))
        for (caller, callee), ids in sorted(call_ids.items())
    )
    service_keys = {
        span.service_name: span.service_key for span in batch.spans if span.service_name
    }
    symptom = service_keys.get("storefront")
    keys, _ = generate_candidates(report.signals, calls, symptom)
    feature_rows = candidate_features(
        report, calls, keys, symptom, CANONICAL_QUESTION, version=feature_schema
    )
    heuristic = {
        item.service_key: item.score
        for item in score_candidates(report.signals, calls, keys, symptom)
    }
    return (
        {
            "episode_id": str(manifest.episode_id),
            "cutoff_ns": cutoff_ns,
            "features": feature_rows,
            "heuristic": heuristic,
            "signal_input_hash": report.input_hash,
        },
        source.sha256,
        bytes_sha256(manifest_file.read_bytes()),
        {name: key for name, key in service_keys.items() if key is not None},
    )


def build_feature_table(
    public_root: Path,
    gold_root: Path,
    *,
    splits: tuple[str, ...] = ("train", "dev"),
    feature_schema: str = FEATURE_SCHEMA_VERSION,
) -> tuple[tuple[FeatureRow, ...], dict[str, Any]]:
    """Never open a truth file outside the declared train/development splits."""
    if not splits or any(split not in {"train", "dev"} for split in splits):
        raise ValueError("training table may read only train/dev splits")
    if (
        public_root.resolve() == gold_root.resolve()
        or gold_root.resolve() in public_root.resolve().parents
    ):
        raise ValueError("public and private roots must be separate")
    private_file = gold_root / "training_access.json"
    if private_file.is_symlink():
        raise ValueError("unsafe private index")
    index_bytes = private_file.read_bytes()
    index = json.loads(index_bytes)
    selected = [item for item in index["episodes"] if item["split"] in splits]
    if any(item["split"] not in {"train", "dev"} for item in index["episodes"]):
        raise ValueError("training access manifest contains held-out episodes")
    rows: list[FeatureRow] = []
    episode_stats: list[dict[str, Any]] = []
    for item in selected:
        episode_id = str(UUID(item["episode_id"]))
        if episode_id != item["episode_id"] or (item["group_id"], item["split"]) not in {
            (group["group_id"], group["split"]) for group in index["groups"]
        }:
            raise ValueError("private split identity mismatch")
        public, source_hash, manifest_hash, name_keys = _public_features(
            public_root / episode_id, feature_schema
        )
        if public["episode_id"] != episode_id:
            raise ValueError("public episode identity mismatch")
        truth_file = gold_root / episode_id / "truth.json"
        if truth_file.is_symlink():
            raise ValueError("unsafe private truth")
        truth_bytes = truth_file.read_bytes()
        if bytes_sha256(truth_bytes) != item["truth_hash"]:
            raise ValueError("private truth hash mismatch")
        truth = json.loads(truth_bytes)
        if truth["split"] != item["split"] or truth["scenario_group"] != item["group_id"]:
            raise ValueError("private truth scope mismatch")
        identifiable = bool(truth["identifiable"] and truth["accepted_target_services"])
        accepted = {
            name_keys[name] for name in truth["accepted_target_services"] if name in name_keys
        }
        available = {row.service_key for row in public["features"]}
        target_in_candidates = bool(accepted & available) if identifiable else False
        episode_stats.append(
            {
                "episode_id": episode_id,
                "group_id": item["group_id"],
                "split": item["split"],
                "identifiable": identifiable,
                "target_in_candidates": target_in_candidates,
                "candidate_count": len(public["features"]),
                "manifest_hash": manifest_hash,
                "source_hash": source_hash,
                "truth_hash": item["truth_hash"],
            }
        )
        for feature in public["features"]:
            rows.append(
                FeatureRow(
                    episode_id=episode_id,
                    group_id=item["group_id"],
                    split=item["split"],
                    cutoff_ns=public["cutoff_ns"],
                    candidate_key=feature.service_key,
                    features=feature.values,
                    evidence_ids=feature.evidence_ids,
                    heuristic_score=public["heuristic"][feature.service_key],
                    label=int(feature.service_key in accepted) if identifiable else None,
                    identifiable=identifiable,
                    target_in_candidates=target_in_candidates,
                    source_hash=source_hash,
                    signal_input_hash=public["signal_input_hash"],
                )
            )
    metadata = {
        "feature_schema_version": feature_schema,
        "feature_names": (
            TRANSFER_FEATURE_NAMES
            if feature_schema == TRANSFER_FEATURE_SCHEMA_VERSION
            else FEATURE_NAMES
        ),
        "private_index_hash": bytes_sha256(index_bytes),
        "splits": splits,
        "episodes": episode_stats,
        "rows_hash": semantic_sha256({"rows": [asdict(row) for row in rows]}),
    }
    return tuple(rows), metadata
