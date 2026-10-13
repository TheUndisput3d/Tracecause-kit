"""Provisional direct-span edge reconstruction on frozen synthetic episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from scripts.fixtures import TOPOLOGIES
from tracecause.contracts.models import EpisodeManifest
from tracecause.evaluation.metrics import rate, score_edges
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ranking.table import _fact
from tracecause.telemetry.signals import infer_calls

from .questions import ROOT


def evaluate(
    public_root: Path, gold_root: Path, output_root: Path, *, split: str
) -> dict[str, Any]:
    if split not in {"train", "dev", "test"}:
        raise ValueError("invalid frozen split")
    public_index = json.loads((public_root / "index.json").read_text())
    private_index_bytes = (gold_root / "dataset.json").read_bytes()
    private_index = json.loads(private_index_bytes)
    public_by_id = {item["episode_id"]: item for item in public_index["episodes"]}
    if len(public_by_id) != len(public_index["episodes"]):
        raise ValueError("duplicate public episode")
    groups = {f"g{item['index']:02d}": item for item in private_index["group_assignments"]}
    if len(groups) != len(private_index["group_assignments"]):
        raise ValueError("duplicate private scenario group")
    selected = sorted(
        (item for item in private_index["episodes"] if item["split"] == split),
        key=lambda item: item["episode_id"],
    )
    if len({item["episode_id"] for item in selected}) != len(selected):
        raise ValueError("duplicate selected episode")
    if len(selected) != {"train": 70, "dev": 25, "test": 25}[split] or any(
        groups[item["group_id"]]["split"] != split for item in selected
    ):
        raise ValueError("frozen group split mismatch")
    rows: list[dict[str, Any]] = []
    for item in selected:
        started = time.perf_counter()
        episode_id = item["episode_id"]
        public = public_by_id.get(episode_id)
        if public is None:
            raise ValueError("public episode missing from index")
        episode_dir = public_root / episode_id
        manifest_file, source_file = episode_dir / "manifest.json", episode_dir / "traces.jsonl"
        if any(path.is_symlink() for path in (episode_dir, manifest_file, source_file)):
            raise ValueError("unsafe public episode path")
        manifest_bytes = manifest_file.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != public["manifest_hash"]:
            raise ValueError("public manifest hash mismatch")
        manifest = EpisodeManifest.model_validate_json(manifest_bytes)
        if manifest.episode_id != UUID(episode_id):
            raise ValueError("public manifest episode mismatch")
        with source_file.open("rb") as source:
            batch = normalize_otlp(
                source,
                encoding=InputEncoding.OTLP_JSONL_V1,
                workspace_id=manifest.workspace_id,
                episode_id=manifest.episode_id,
                expected_source_hash=public["source_hash"],
                source_name="traces.jsonl",
            )
        if batch.fatal or batch.rejected_count or batch.source_hash != public["source_hash"]:
            raise ValueError("public source failed normalization")
        facts = tuple(_fact(span, batch.redacted_hash) for span in batch.spans)
        pairs, unsupported = infer_calls(facts)
        predicted = sorted(
            {
                (pair.caller.service_key, pair.callee.service_key)
                for pair in pairs
                if pair.caller.service_key is not None and pair.callee.service_key is not None
            }
        )
        # Private topology is opened only after predictions are derived from public spans.
        truth_file = gold_root / episode_id / "truth.json"
        if truth_file.is_symlink():
            raise ValueError("unsafe private truth path")
        truth_bytes = truth_file.read_bytes()
        if hashlib.sha256(truth_bytes).hexdigest() != item["truth_hash"]:
            raise ValueError("private truth hash mismatch")
        truth = json.loads(truth_bytes)
        if (
            truth["episode_id"] != episode_id
            or truth["split"] != split
            or truth["scenario_group"] != item["group_id"]
            or truth["source_hash"] != public["source_hash"]
            or truth["topology"] != groups[item["group_id"]]["topology"]
        ):
            raise ValueError("public/private episode mismatch")
        topology = TOPOLOGIES[truth["topology"]]
        name_keys = {
            span.service_name: span.service_key
            for span in batch.spans
            if span.service_name and span.service_key
        }
        expected = sorted(
            (name_keys[caller], name_keys[callee])
            for caller, children in topology.items()
            for callee in children
            if caller in name_keys and callee in name_keys
        )
        score = score_edges(predicted, expected)
        names = {key: name for name, key in name_keys.items()}
        rows.append(
            {
                "episode_id": episode_id,
                "group_id": item["group_id"],
                "split": split,
                "topology": truth["topology"],
                "source_sha256": batch.source_hash,
                "truth_sha256": item["truth_hash"],
                "score": asdict(score),
                "predicted_edges": [[names[a], names[b]] for a, b in predicted],
                "observable_known_edges": [[names[a], names[b]] for a, b in expected],
                "observed_support_pairs": len(pairs),
                "unsupported_server_spans": dict(Counter(unsupported.values())),
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
    hits = sum(row["score"]["true_positive_edges"] for row in rows)
    predicted_count = sum(row["score"]["predicted_edges"] for row in rows)
    known_count = sum(row["score"]["observable_known_edges"] for row in rows)
    unsupported_counts: Counter[str] = Counter()
    for row in rows:
        unsupported_counts.update(row["unsupported_server_spans"])
    summary = {
        "schema_version": "1.0",
        "status": "provisional_generator_topology_not_human_reviewed",
        "scope": "direct_span_inference_only_not_database_projection",
        "origin": "synthetic",
        "split": split,
        "episodes": len(rows),
        "micro_precision": asdict(rate(hits, predicted_count)),
        "micro_recall": asdict(rate(hits, known_count)),
        "unsupported_server_spans": dict(unsupported_counts),
        "private_index_sha256": hashlib.sha256(private_index_bytes).hexdigest(),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    output_file = output_root / f"synthetic_{split}_graph_reconstruction.json"
    output_file.write_text(
        json.dumps({"summary": summary, "episodes": rows}, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=ROOT / "data/generated/seed-20261010")
    parser.add_argument("--gold-root", type=Path, default=ROOT / "data/gold/seed-20261010")
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-provisional")
    parser.add_argument("--split", choices=("train", "dev", "test"), default="test")
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.public_root, args.gold_root, args.output_root, split=args.split),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
