"""Source-located captured CLIENT/SERVER pair packets, without automatic approval."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from tracecause.contracts.models import EpisodeManifest, NormalizedSpan
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding

from ..capture.validation import timestamp_ns
from .prepare import ROOT, digest, write_json

MODES = {"payment_error", "inventory_delay", "catalog_error", "caller_timeout", "healthy"}


def located(span: NormalizedSpan) -> dict[str, Any]:
    fields = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "service_name",
        "kind",
        "start_ns",
        "end_ns",
        "source_record_offset",
        "source_line_number",
        "source_resource_index",
        "source_scope_index",
        "source_span_index",
    )
    result = {key: getattr(span, key) for key in fields}
    result["http_status"] = span.safe_attributes.get("http.status_code")
    return result


def extract(episode_id: str) -> dict[str, Any]:
    public = ROOT / "data/captured" / episode_id
    truth_path = ROOT / "data/captured_private" / episode_id / "truth.json"
    truth = json.loads(truth_path.read_bytes())
    manifest = EpisodeManifest.model_validate_json((public / "manifest.json").read_bytes())
    with (public / "traces.jsonl").open("rb") as stream:
        batch = normalize_otlp(
            stream,
            encoding=InputEncoding.OTLP_JSONL_V1,
            workspace_id=manifest.workspace_id,
            episode_id=manifest.episode_id,
            expected_source_hash=truth["source_sha256"],
            source_name="traces.jsonl",
        )
    if batch.fatal or batch.rejected_count or str(manifest.episode_id) != episode_id:
        raise ValueError("captured pair source failed normalization or scope check")
    spans = {(span.trace_id, span.span_id): span for span in batch.spans}
    intervals = truth["actual_intervals"]
    windows = {
        stage: (timestamp_ns(intervals[f"{stage}_start"]), timestamp_ns(intervals[f"{stage}_end"]))
        for stage in ("baseline", "intervention", "recovery")
    }
    samples = {}
    for span in batch.spans:
        if span.kind != "SERVER":
            continue
        parent = spans.get((span.trace_id, span.parent_span_id))
        if parent is None or parent.kind != "CLIENT" or parent.service_key == span.service_key:
            continue
        stage = next(
            (name for name, (left, right) in windows.items() if left <= int(span.start_ns) < right),
            None,
        )
        if stage is None:
            continue
        key = f"{stage}:{parent.service_name}->{span.service_name}"
        if key not in samples:
            samples[key] = dict(pair_id=key, caller=located(parent), callee=located(span))
    return {
        "episode_id": episode_id,
        "key": truth["key"],
        "mode": truth["mode"],
        "split": truth["split"],
        "source_sha256": batch.source_hash,
        "manifest_sha256": digest(public / "manifest.json"),
        "truth_sha256": digest(truth_path),
        "selection": "first source-order direct pair per observed edge and stage",
        "pairs": [samples[key] for key in sorted(samples)],
        "status": "packet_only_not_reviewed",
    }


def validate(packet: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if (
        review.get("review_origin") != "ai"
        or review.get("independent_human_review") is not False
        or not review.get("reviewer")
        or not review.get("review_date")
        or review.get("episode_id") != packet["episode_id"]
    ):
        raise ValueError("pair review identity or attestation is missing")
    pairs = {row["pair_id"]: row for row in packet["pairs"]}
    if not pairs or len(pairs) != len(packet["pairs"]):
        raise ValueError("pair packet is empty or contains duplicate pairs")
    date.fromisoformat(review["review_date"])
    judgments = review["judgments"]
    if (
        len({row["pair_id"] for row in judgments}) != len(judgments)
        or {row["pair_id"] for row in judgments} != pairs.keys()
    ):
        raise ValueError("pair review coverage differs")
    for row in judgments:
        if (
            row["judgment"] not in {"supported", "unsupported", "unavailable"}
            or not row["rationale"].strip()
        ):
            raise ValueError("pair review judgment or rationale is missing")
    return {
        "episode_id": packet["episode_id"],
        "mode": packet["mode"],
        "reviewed_pairs": len(judgments),
        "supported_pairs": sum(row["judgment"] == "supported" for row in judgments),
    }


def run(root: Path) -> dict[str, Any]:
    audit = json.loads((ROOT / "reports/capture/audit.json").read_bytes())
    first: dict[str, dict[str, Any]] = {}
    for row in sorted(audit["runs"], key=lambda item: item["key"]):
        if row["split"] != "smoke":
            first.setdefault(row["mode"], row)
    reviewed = []
    for mode, row in sorted(first.items()):
        path = root / f"{mode}.packet.json"
        if not path.exists():
            write_json(path, extract(row["episode_id"]))
        packet = json.loads(path.read_bytes())
        if (
            packet["episode_id"] != row["episode_id"]
            or packet["source_sha256"] != row["source_sha256"]
        ):
            raise ValueError("captured pair selection or source changed")
        decision = root / f"{mode}.ai_review.json"
        if decision.exists():
            review = json.loads(decision.read_bytes())
            if review["packet_sha256"] != digest(path):
                raise ValueError("reviewed captured pair packet changed")
            reviewed.append(validate(packet, review))
    summary = {
        "status": "all_modes_ai_reviewed" if {r["mode"] for r in reviewed} == MODES else "partial",
        "review_origin": "ai",
        "independent_human_review": False,
        "packet_modes": sorted(first),
        "missing_modes": sorted(MODES - first.keys()),
        "reviews": reviewed,
        "limitation": "Purposive direct-call sample; not a random accuracy or causal estimate.",
    }
    write_json(root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-captured-pairs")
    args = parser.parse_args()
    print(json.dumps(run(args.output_root)))


if __name__ == "__main__":
    main()
