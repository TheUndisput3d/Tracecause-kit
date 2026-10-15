"""Offline-only frozen corpus leakage scan against private synthetic truth IDs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tracecause.documents.markdown import parse_document

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/runbooks/demo"
GOLD = ROOT / "data/gold"
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
PRIVATE_FIELD_PATTERN = re.compile(
    r"\b(target_service|accepted_target_services|scenario_group|intervention_start|"
    r"injected_target|fault_flag)\b",
    re.IGNORECASE,
)


def scan() -> dict[str, object]:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    private_ids: set[str] = set()
    for path in GOLD.glob("**/truth.json"):
        truth = json.loads(path.read_text())
        private_ids.add(truth["episode_id"])
        private_ids.add(truth["source_hash"])
        private_ids.add(truth["semantic_hash"])
        private_ids.add(str(truth["seed"]))
        private_ids.add(truth["intervention_start"])
    findings: list[str] = []
    documents = []
    for row in manifest["documents"]:
        path = CORPUS / f"{row['source_id']}.md"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            findings.append(f"{row['source_id']}:manifest_hash")
        document = parse_document(raw)
        if document.header.source_id != row["source_id"]:
            findings.append(f"{row['source_id']}:source_id")
        text = document.source_text
        if UUID_PATTERN.search(text) or PRIVATE_FIELD_PATTERN.search(text):
            findings.append(f"{row['source_id']}:private_identifier_pattern")
        if any(value and value in text for value in private_ids):
            findings.append(f"{row['source_id']}:private_truth_value")
        documents.append(document)
    report = {
        "corpus_sha256": hashlib.sha256((CORPUS / "manifest.json").read_bytes()).hexdigest(),
        "documents_scanned": len(documents),
        "private_truth_files_scanned": sum(1 for _ in GOLD.glob("**/truth.json")),
        "future_historical_notes": sum(
            item.header.document_type == "historical_note" for item in documents
        ),
        "findings": findings,
    }
    return report


def main() -> int:
    report = scan()
    print(json.dumps(report, sort_keys=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
