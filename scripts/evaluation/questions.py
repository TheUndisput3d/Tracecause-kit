"""Create deterministic synthetic question proposals for independent review."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tracecause.contracts.models import EpisodeManifest

ROOT = Path(__file__).resolve().parents[2]
TEXT = {
    "direct_fault_location": "Which service, if any, first shows a new abnormal signal?",
    "dependency_path": (
        "Which supported dependency path connects the customer request to the abnormal service?"
    ),
    "temporal_change": "What changed between baseline and the later observation window?",
    "related_incident": "Which diagnostic document is relevant to the observed change, and why?",
    "ambiguous_diagnosis": (
        "What can the available traces establish about the cause, and what remains ambiguous?"
    ),
    "unanswerable": "Did a service outside MiniShop have an outage?",
}
CATEGORIES = tuple(TEXT)
REVIEW_FIELDS = [
    "question_id",
    "episode_id",
    "split",
    "category",
    "question",
    "accepted_targets",
    "approved_question",
    "approved_answerability",
    "approved_targets",
    "reviewer",
    "review_date",
    "evidence_ids_and_grades",
    "disagreement_notes",
    "independent_review_attestation",
]


def generate(public_root: Path, gold_root: Path, output_root: Path) -> dict[str, object]:
    public_index = json.loads((public_root / "index.json").read_text())
    private_index = json.loads((gold_root / "dataset.json").read_text())
    public_rows = {item["episode_id"]: item for item in public_index["episodes"]}
    private_rows = {item["episode_id"]: item for item in private_index["episodes"]}
    if len(public_rows) != 120 or set(public_rows) != set(private_rows):
        raise ValueError("synthetic public/private episode index mismatch")
    drafts = []
    for index, episode_id in enumerate(sorted(public_rows)):
        public_row, private_row = public_rows[episode_id], private_rows[episode_id]
        episode_dir = public_root / episode_id
        truth_file = gold_root / episode_id / "truth.json"
        truth_bytes = truth_file.read_bytes()
        if hashlib.sha256(truth_bytes).hexdigest() != private_row["truth_hash"]:
            raise ValueError("private truth hash mismatch")
        truth = json.loads(truth_bytes)
        manifest_bytes = (episode_dir / "manifest.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != public_row["manifest_hash"]:
            raise ValueError("public manifest hash mismatch")
        manifest = EpisodeManifest.model_validate_json(manifest_bytes)
        if manifest.sources[0].sha256 != public_row["source_hash"] or (
            truth["source_hash"] != public_row["source_hash"]
        ):
            raise ValueError("public/private source mismatch")
        if truth["split"] != private_row["split"]:
            raise ValueError("private split mismatch")
        if truth["mechanism"] == "healthy":
            category = "unanswerable"
        elif not truth["identifiable"]:
            category = "ambiguous_diagnosis"
        else:
            category = CATEGORIES[index % 4]
        drafts.append(
            {
                "schema_version": "1.0",
                "question_id": str(uuid5(NAMESPACE_URL, f"tracecause-question-v1:{episode_id}")),
                "episode_id": episode_id,
                "origin": "synthetic",
                "split": truth["split"],
                "group_id": truth["scenario_group"],
                "cutoff": manifest.event_interval.end.isoformat(),
                "category": category,
                "question": TEXT[category],
                "answerability": "unanswerable"
                if category == "unanswerable"
                else "ambiguous"
                if category == "ambiguous_diagnosis"
                else "proposed_answerable",
                "accepted_target_services": truth["accepted_target_services"],
                "identifiable": truth["identifiable"],
                "evidence_judgments": [],
                "authorship": "implementation_template",
                "review_status": "draft_requires_independent_human_review",
                "reviewer": None,
            }
        )
    if len({item["question_id"] for item in drafts}) != len(drafts):
        raise ValueError("duplicate question ID")
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "draft_questions.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "draft_requires_independent_human_review",
                "questions": drafts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    review_path = output_root / "review_sheet.csv"
    fields = REVIEW_FIELDS
    existing: dict[str, dict[str, str]] = {}
    if review_path.is_file():
        with review_path.open(newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames not in (fields, fields[:-1]):
                raise ValueError("review sheet columns changed; migrate reviewed data explicitly")
            for row in reader:
                question_id = row.get("question_id", "")
                if not question_id or question_id in existing:
                    raise ValueError("invalid or duplicate existing review row")
                existing[question_id] = row
    review_rows = []
    for item in drafts:
        fixed = {
            "question_id": item["question_id"],
            "episode_id": item["episode_id"],
            "split": item["split"],
            "category": item["category"],
            "question": item["question"],
            "accepted_targets": ";".join(item["accepted_target_services"]),
        }
        previous = existing.pop(item["question_id"], None)
        if previous is not None and any(previous.get(key) != value for key, value in fixed.items()):
            raise ValueError("existing review row conflicts with frozen question draft")
        review_rows.append({**(previous or {}), **fixed})
    if existing:
        raise ValueError("review sheet contains a question absent from the frozen draft")
    temporary_path = review_path.with_suffix(".csv.tmp")
    with temporary_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(review_rows)
    temporary_path.replace(review_path)
    return {
        "count": len(drafts),
        "split_counts": dict(Counter(item["split"] for item in drafts)),
        "category_counts": dict(Counter(item["category"] for item in drafts)),
        "status": "draft_requires_independent_human_review",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=ROOT / "data/generated/seed-20261010")
    parser.add_argument("--gold-root", type=Path, default=ROOT / "data/gold/seed-20261010")
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-questions")
    args = parser.parse_args()
    print(json.dumps(generate(args.public_root, args.gold_root, args.output_root), sort_keys=True))


if __name__ == "__main__":
    main()
