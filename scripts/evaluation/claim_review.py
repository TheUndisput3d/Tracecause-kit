"""Validate explicit claim-review decisions and preserve disagreement/coverage counts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .prepare import ROOT, digest, write_json
from .systems import SYSTEMS


def captured_packet(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the first schedule key per mode, without looking at model outcomes."""
    modes = {"payment_error", "inventory_delay", "catalog_error", "caller_timeout", "healthy"}
    if len({row["run_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate captured prediction")
    if {row["category"] for row in rows} != modes:
        raise ValueError("captured claim packet requires all five modes")
    selected = []
    for mode in sorted(modes):
        eligible = [row for row in rows if row["category"] == mode]
        first = min(row["group_id"] for row in eligible)
        chosen = [row for row in eligible if row["group_id"] == first]
        if (
            len(chosen) != len(SYSTEMS)
            or {row["system"] for row in chosen} != set(SYSTEMS)
            or len({row["episode_id"] for row in chosen}) != 1
            or any(row["track"] != "canonical" for row in chosen)
        ):
            raise ValueError("captured claim sample lacks one canonical output per system")
        selected.extend(sorted(chosen, key=lambda row: row["system"]))
    return {
        "origin": "captured_demo",
        "selection": "first schedule key per mode, all five systems, no outcome-based selection",
        "limitation": "Purposive sample spanning train/dev/test; not held-out claim accuracy.",
        "runs": selected,
    }


def validate(packet: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if review["review_origin"] != "ai" or review["independent_human_review"] is not False:
        raise ValueError("this worksheet records explicit AI review only")
    if not review.get("reviewer") or not review.get("review_date"):
        raise ValueError("claim reviewer and date are required")
    runs = {row["run_id"]: row for row in packet["runs"]}
    if not runs or len(runs) != len(packet["runs"]):
        raise ValueError("empty or duplicate review packet run")
    reviewed = review["reviews"]
    if (
        len({row["run_id"] for row in reviewed}) != len(reviewed)
        or {row["run_id"] for row in reviewed} != runs.keys()
    ):
        raise ValueError("claim review coverage differs from packet")
    counts: Counter[str] = Counter()
    cited_claims = 0
    for row in reviewed:
        original = runs[row["run_id"]]
        for key in ("episode_id", "system", "category"):
            if row[key] != original[key]:
                raise ValueError("reviewed claim run identity differs")
        expected = {
            claim["claim_id"]: claim for claim in original["generation"]["narrative"]["claims"]
        }
        judgments = row["claims"]
        if (
            len({j["claim_id"] for j in judgments}) != len(judgments)
            or {j["claim_id"] for j in judgments} != expected.keys()
        ):
            raise ValueError("claim review is missing or duplicating claims")
        for judgment in judgments:
            claim = expected[judgment["claim_id"]]
            if any(judgment[key] != claim[key] for key in ("claim_type", "evidence_ids")):
                raise ValueError("claim review changed the cited claim")
            if judgment["judgment"] not in {"supported", "partial", "unsupported", "unavailable"}:
                raise ValueError("unknown claim support judgment")
            if not judgment["rationale"].strip():
                raise ValueError("claim judgment rationale is required")
            counts[judgment["judgment"]] += 1
            cited_claims += bool(claim["evidence_ids"])
    total = sum(counts.values())
    return {
        "review_origin": "ai",
        "independent_human_review": False,
        "outputs": len(reviewed),
        "episodes": len({r["episode_id"] for r in reviewed}),
        "claim_counts": dict(counts),
        "strict_support_numerator": counts["supported"],
        "strict_support_denominator": total,
        "strict_support_rate": counts["supported"] / total if total else None,
        "structurally_cited_claims": cited_claims,
        "structured_claims": total,
        "structural_citation_coverage": cited_claims / total if total else None,
        "coverage_limitation": "Presence of a citation is separate from semantic support.",
        "inter_reviewer_agreement": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "reports/phase13-claim-review")
    parser.add_argument("--packet-from", type=Path, help="Completed full captured benchmark root")
    args = parser.parse_args()
    root = args.root
    if args.packet_from:
        completion = json.loads((args.packet_from / "completion.json").read_bytes())
        scored = args.packet_from / "scored_predictions.json"
        rows = json.loads(scored.read_bytes())
        if (
            completion["status"] != "captured_transfer_complete"
            or completion["episodes"] != 24
            or completion["runs"] != len(rows)
            or len(rows) != 120
        ):
            raise ValueError("claim packet requires a completed full captured benchmark")
        packet = {**captured_packet(rows), "scored_predictions_sha256": digest(scored)}
        path = root / "packet.json"
        if path.exists() and json.loads(path.read_bytes()) != packet:
            raise ValueError("claim packet differs; preserve existing review and use a new root")
        write_json(path, packet)
        print(json.dumps({"status": "packet_only_not_reviewed", "outputs": len(packet["runs"])}))
        return
    packet_path, review_path = root / "packet.json", root / "ai_review.json"
    packet, review = json.loads(packet_path.read_bytes()), json.loads(review_path.read_bytes())
    if review["packet_sha256"] != digest(packet_path):
        raise ValueError("claim packet hash changed")
    result = validate(packet, review)
    write_json(root / "validated_summary.json", {**result, "review_sha256": digest(review_path)})
    with (root / "claims.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "run_id",
                "episode_id",
                "system",
                "category",
                "claim_id",
                "claim_type",
                "judgment",
                "rationale",
            ],
        )
        writer.writeheader()
        for row in review["reviews"]:
            for claim in row["claims"]:
                writer.writerow(
                    {
                        **{key: row[key] for key in ("run_id", "episode_id", "system", "category")},
                        **{
                            key: claim[key]
                            for key in ("claim_id", "claim_type", "judgment", "rationale")
                        },
                    }
                )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
