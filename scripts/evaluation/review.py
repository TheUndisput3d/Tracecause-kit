"""Validate independently attested question reviews before producing offline judgments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .questions import REVIEW_FIELDS, ROOT

ANSWERABILITY = {"answerable", "ambiguous", "unanswerable"}
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _judgments(raw: str, *, answerability: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("evidence judgments must be a JSON array") from exc
    if not isinstance(value, list):
        raise ValueError("evidence judgments must be a JSON array")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "evidence_id",
            "grade",
            "source_hash",
            "rationale",
        }:
            raise ValueError("evidence judgment fields are incomplete")
        evidence_id = item["evidence_id"]
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or evidence_id in seen
            or type(item["grade"]) is not int
            or item["grade"] not in {0, 1, 2}
            or not isinstance(item["source_hash"], str)
            or HASH.fullmatch(item["source_hash"]) is None
            or not isinstance(item["rationale"], str)
            or not item["rationale"].strip()
        ):
            raise ValueError("invalid or duplicate evidence judgment")
        seen.add(evidence_id)
    if answerability == "answerable" and not any(item["grade"] > 0 for item in value):
        raise ValueError("answerable question needs a relevant judged evidence item")
    return value


def validate_review(
    draft_path: Path,
    review_path: Path,
    output_path: Path,
    *,
    reviewer_kind: Literal["human", "ai"] = "human",
) -> dict[str, Any]:
    if reviewer_kind not in {"human", "ai"}:
        raise ValueError("unsupported review origin")
    status = f"{reviewer_kind}_attested_requires_evidence_resolution"
    draft_bytes = draft_path.read_bytes()
    review_bytes = review_path.read_bytes()
    draft = json.loads(draft_bytes)
    if draft.get("status") != "draft_requires_independent_human_review":
        raise ValueError("question draft has an unexpected status")
    questions = draft["questions"]
    by_id = {item["question_id"]: item for item in questions}
    if len(by_id) != len(questions):
        raise ValueError("duplicate draft question")
    with review_path.open(newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != REVIEW_FIELDS:
            raise ValueError("review sheet has unexpected columns")
        rows = list(reader)
    if len(rows) != len(questions):
        raise ValueError("review sheet is incomplete or contains extra rows")
    reviewed = []
    seen: set[str] = set()
    for row in rows:
        question_id = row["question_id"]
        original = by_id.get(question_id)
        if original is None or question_id in seen:
            raise ValueError("unknown or duplicate reviewed question")
        seen.add(question_id)
        fixed = {
            "episode_id": original["episode_id"],
            "split": original["split"],
            "category": original["category"],
            "question": original["question"],
            "accepted_targets": ";".join(original["accepted_target_services"]),
        }
        if any(row[key] != value for key, value in fixed.items()):
            raise ValueError("reviewed row conflicts with the frozen draft")
        attestation = "yes" if reviewer_kind == "human" else "ai_reviewed"
        if row["independent_review_attestation"].strip().lower() != attestation:
            raise ValueError(f"{reviewer_kind} review attestation must be {attestation}")
        reviewer = row["reviewer"].strip()
        if not reviewer or reviewer == original["authorship"]:
            raise ValueError("independent reviewer identity is required")
        try:
            date.fromisoformat(row["review_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("review date must be ISO YYYY-MM-DD") from exc
        approved_question = row["approved_question"].strip()
        if not 1 <= len(approved_question) <= 2000:
            raise ValueError("approved question is missing or too long")
        answerability = row["approved_answerability"].strip()
        if answerability not in ANSWERABILITY:
            raise ValueError("approved answerability is missing or invalid")
        targets = [item.strip() for item in row["approved_targets"].split(";") if item.strip()]
        if len(targets) != len(set(targets)) or answerability == "answerable" and not targets:
            raise ValueError("approved target set is invalid")
        judgments = _judgments(row["evidence_ids_and_grades"], answerability=answerability)
        reviewed.append(
            {
                **original,
                "question": approved_question,
                "answerability": answerability,
                "accepted_target_services": targets,
                "identifiable": original["identifiable"],
                "evidence_judgments": judgments,
                "authorship": "independently_reviewed_draft"
                if reviewer_kind == "human"
                else "ai_reviewed_draft",
                "review_status": status,
                "review_origin": reviewer_kind,
                "reviewer": reviewer,
                "review_date": row["review_date"],
                "disagreement_notes": row["disagreement_notes"],
            }
        )
    result = {
        "schema_version": "1.0",
        "status": status,
        "review_origin": reviewer_kind,
        "independent_human_review": reviewer_kind == "human",
        "draft_sha256": hashlib.sha256(draft_bytes).hexdigest(),
        "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "question_count": len(reviewed),
        "questions": reviewed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    temporary.replace(output_path)
    return {key: value for key, value in result.items() if key != "questions"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--draft", type=Path, default=ROOT / "reports/phase13-questions/draft_questions.json"
    )
    parser.add_argument(
        "--review", type=Path, default=ROOT / "reports/phase13-questions/review_sheet.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/phase13-questions/reviewed_questions.json"
    )
    parser.add_argument("--reviewer-kind", choices=("human", "ai"), default="human")
    args = parser.parse_args()
    try:
        report = validate_review(
            args.draft, args.review, args.output, reviewer_kind=args.reviewer_kind
        )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"question review validation failed: {exc}\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
