"""Validate private MiniShop human review without feeding labels to runtime."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .audit import REVIEW_FIELDS
from .minishop import MODE_ROLE, ROOT, SCHEDULE, dump
from .validation import frozen_schedule

REVIEW_COLUMNS = set(REVIEW_FIELDS[6:])


def validate_review(
    audit_path: Path,
    sheet_path: Path,
    output_path: Path,
    *,
    reviewer_kind: Literal["human", "ai"] = "human",
) -> dict[str, Any]:
    if reviewer_kind not in {"human", "ai"}:
        raise ValueError("unsupported review origin")
    audit_bytes, sheet_bytes = audit_path.read_bytes(), sheet_path.read_bytes()
    audit = json.loads(audit_bytes)
    schedule = frozen_schedule(SCHEDULE)
    if audit.get("schedule_sha256") != hashlib.sha256(SCHEDULE.read_bytes()).hexdigest():
        raise ValueError("capture audit does not match the frozen schedule")
    expected_by_key = {item["key"]: item for item in schedule["runs"]}
    runs = {item["episode_id"]: item for item in audit["runs"] if item["split"] != "smoke"}
    if len(runs) != audit["benchmark_runs"]:
        raise ValueError("audit has duplicate or missing benchmark episodes")
    with sheet_path.open(newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != REVIEW_FIELDS:
            raise ValueError("capture review sheet has unexpected columns")
        rows = list(reader)
    if len(rows) != len(runs):
        raise ValueError("capture review sheet does not match audit")
    seen: set[str] = set()
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        episode_id = row["episode_id"]
        item = runs.get(episode_id)
        if item is None or episode_id in seen:
            raise ValueError("unknown or duplicate reviewed capture episode")
        seen.add(episode_id)
        expected = expected_by_key.get(item["key"])
        if expected is None or any(item[key] != expected[key] for key in ("split", "mode", "seed")):
            raise ValueError("audit run conflicts with frozen schedule")
        fixed = {
            "key": item["key"],
            "split": item["split"],
            "intended_mode": item["mode"],
            "observed_effect": str(item["observable_effect"]),
            "proposed_targets": ";".join(item["proposed_target_services"]),
        }
        if any(row[key] != value for key, value in fixed.items()):
            raise ValueError("capture review row conflicts with frozen audit")
        if not any(row[key].strip() for key in REVIEW_COLUMNS):
            continue
        attestation = "yes" if reviewer_kind == "human" else "ai_reviewed"
        if row["independent_review_attestation"].strip().lower() != attestation:
            raise ValueError(f"{reviewer_kind} capture review attestation must be {attestation}")
        reviewer = row["reviewer"].strip()
        if not reviewer:
            raise ValueError("capture reviewer identity is required")
        try:
            date.fromisoformat(row["review_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("capture review date must be ISO YYYY-MM-DD") from exc
        if row["service_mapping_confirmed"].strip().lower() != "yes":
            raise ValueError("capture service mapping must be confirmed")
        intervention = row["intervention_confirmed"].strip().lower()
        required = "not_applicable" if item["mode"] == "healthy" else "yes"
        if intervention != required:
            raise ValueError("capture intervention confirmation is missing")
        identifiable = row["identifiable"].strip().lower()
        if identifiable not in {"yes", "no"}:
            raise ValueError("capture identifiability must be yes or no")
        targets = [key.strip() for key in row["accepted_targets"].split(";") if key.strip()]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate capture accepted target")
        if item["mode"] == "healthy" and (targets or identifiable != "no"):
            raise ValueError("healthy capture must have no target and be unidentifiable")
        if item["mode"] != "healthy" and identifiable == "yes" and not targets:
            raise ValueError("identifiable capture requires an accepted target")
        if any(target not in MODE_ROLE.values() and target != "storefront" for target in targets):
            raise ValueError("capture target outside MiniShop roles")
        if not row["evidence_notes"].strip():
            raise ValueError("capture review needs evidence notes")
        reviewed.append(
            {
                "episode_id": episode_id,
                "key": item["key"],
                "split": item["split"],
                "mode": item["mode"],
                "identifiable": identifiable == "yes",
                "accepted_target_services": targets,
                "reviewer": reviewer,
                "review_origin": reviewer_kind,
                "review_date": row["review_date"],
                "evidence_notes": row["evidence_notes"],
                "disagreement_notes": row["disagreement_notes"],
            }
        )
    modes = Counter(item["mode"] for item in reviewed)
    splits = Counter(item["split"] for item in reviewed)
    reviewed_ids = {item["episode_id"] for item in reviewed}
    test_ids = {item["episode_id"] for item in runs.values() if item["split"] == "test"}
    captured_keys = {item["key"] for item in runs.values()}
    if len(captured_keys) != len(runs):
        raise ValueError("capture audit contains duplicate run keys")
    expected_test_keys = {item["key"] for item in schedule["runs"] if item["split"] == "test"}
    complete = (
        captured_keys == set(expected_by_key)
        and not audit.get("quality_issues")
        and all(not item.get("quality_issues") for item in runs.values())
    )
    representative = (
        complete
        and audit["status"] == "complete_pending_human_review"
        and set(modes) == {"healthy", *MODE_ROLE}
        and set(splits) == {"train", "dev", "test"}
    )
    result = {
        "schema_version": "1.0",
        "status": (
            (
                "representative_human_attested"
                if reviewer_kind == "human"
                else "representative_ai_reviewed"
            )
            if representative
            else "partial_review"
        ),
        "review_origin": reviewer_kind,
        "independent_human_review": reviewer_kind == "human",
        "audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "review_sha256": hashlib.sha256(sheet_bytes).hexdigest(),
        "dataset_runs": len(runs),
        "reviewed_runs": len(reviewed),
        "reviewed_by_mode": dict(modes),
        "reviewed_by_split": dict(splits),
        "all_test_runs_reviewed": (
            bool(expected_test_keys)
            and expected_test_keys <= captured_keys
            and test_ids <= reviewed_ids
        ),
        "reviewed_labels": reviewed,
        "limitation": (
            "Reviewer identity and attestation are self-reported; independent provenance "
            "must be checked outside this script."
            if reviewer_kind == "human"
            else "User-authorized AI review; no independent human review."
        ),
    }
    dump(output_path, result)
    return {key: value for key, value in result.items() if key != "reviewed_labels"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "reports/capture/audit.json")
    parser.add_argument("--sheet", type=Path, default=ROOT / "reports/capture/review_sheet.csv")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/capture/review_validation.json"
    )
    parser.add_argument("--require-representative", action="store_true")
    parser.add_argument("--require-all-test", action="store_true")
    parser.add_argument("--reviewer-kind", choices=("human", "ai"), default="human")
    args = parser.parse_args()
    try:
        report = validate_review(
            args.audit, args.sheet, args.output, reviewer_kind=args.reviewer_kind
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"capture review validation failed: {exc}\n")
    print(json.dumps(report, sort_keys=True))
    if args.require_representative and report["status"] not in {
        "representative_human_attested",
        "representative_ai_reviewed",
    }:
        parser.exit(2, "capture review is not representative and complete\n")
    if args.require_all_test and not report["all_test_runs_reviewed"]:
        parser.exit(2, "captured test labels are not fully reviewed\n")


if __name__ == "__main__":
    main()
