"""Review import must reject draft-only and tampered question labels."""

import csv
import json
from pathlib import Path

import pytest
from scripts.evaluation.questions import REVIEW_FIELDS
from scripts.evaluation.review import validate_review


def test_review_requires_independent_attestation_and_preserves_provenance(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    sheet = tmp_path / "review.csv"
    output = tmp_path / "reviewed.json"
    question = {
        "question_id": "question-1",
        "episode_id": "episode-1",
        "split": "test",
        "category": "direct_fault_location",
        "question": "Which service is abnormal?",
        "accepted_target_services": ["payment"],
        "authorship": "implementation_template",
        "identifiable": True,
    }
    draft.write_text(
        json.dumps({"status": "draft_requires_independent_human_review", "questions": [question]})
    )
    row = {
        "question_id": "question-1",
        "episode_id": "episode-1",
        "split": "test",
        "category": "direct_fault_location",
        "question": "Which service is abnormal?",
        "accepted_targets": "payment",
    }

    def write(values: dict[str, str]) -> None:
        with sheet.open("w", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerow(values)

    write(row)
    with pytest.raises(ValueError, match="attestation"):
        validate_review(draft, sheet, output)
    assert not output.exists()
    row.update(
        {
            "independent_review_attestation": "yes",
            "reviewer": "reviewer A",
            "review_date": "2026-10-11",
            "approved_question": "Which service shows the new error?",
            "approved_answerability": "answerable",
            "approved_targets": "payment",
            "evidence_ids_and_grades": json.dumps(
                [
                    {
                        "evidence_id": "observation:123",
                        "grade": 2,
                        "source_hash": "a" * 64,
                        "rationale": "New payment errors are observed.",
                    }
                ]
            ),
        }
    )
    write(row)
    report = validate_review(draft, sheet, output)
    assert report["question_count"] == 1
    approved = json.loads(output.read_text())
    assert approved["status"] == "human_attested_requires_evidence_resolution"
    assert approved["questions"][0]["reviewer"] == "reviewer A"
    assert approved["questions"][0]["evidence_judgments"][0]["grade"] == 2
    with pytest.raises(ValueError, match="ai_reviewed"):
        validate_review(draft, sheet, output, reviewer_kind="ai")
    row["independent_review_attestation"] = "ai_reviewed"
    row["reviewer"] = "Codex"
    write(row)
    ai = validate_review(draft, sheet, output, reviewer_kind="ai")
    assert ai["review_origin"] == "ai" and ai["independent_human_review"] is False
    assert ai["status"] == "ai_attested_requires_evidence_resolution"
    with pytest.raises(ValueError, match="attestation"):
        validate_review(draft, sheet, output)
    prior = output.read_bytes()
    row["accepted_targets"] = "catalog"
    write(row)
    with pytest.raises(ValueError, match="frozen draft"):
        validate_review(draft, sheet, output, reviewer_kind="ai")
    assert output.read_bytes() == prior


def test_review_rejects_unjudged_answerable_question(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    sheet = tmp_path / "review.csv"
    draft.write_text(
        json.dumps(
            {
                "status": "draft_requires_independent_human_review",
                "questions": [
                    {
                        "question_id": "q",
                        "episode_id": "e",
                        "split": "test",
                        "category": "direct_fault_location",
                        "question": "What happened?",
                        "accepted_target_services": ["payment"],
                        "authorship": "implementation_template",
                    }
                ],
            }
        )
    )
    row = {
        "question_id": "q",
        "episode_id": "e",
        "split": "test",
        "category": "direct_fault_location",
        "question": "What happened?",
        "accepted_targets": "payment",
        "approved_question": "What happened?",
        "approved_answerability": "answerable",
        "approved_targets": "payment",
        "reviewer": "reviewer A",
        "review_date": "2026-10-11",
        "evidence_ids_and_grades": "[]",
        "independent_review_attestation": "yes",
    }
    with sheet.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError, match="relevant judged evidence"):
        validate_review(draft, sheet, tmp_path / "reviewed.json")
