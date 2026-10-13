"""Small, explicitly AI-reviewed authored entity-linking diagnostic."""

from __future__ import annotations

import json
from collections import Counter

import yaml

from tracecause.documents.linking import ServiceIdentity, resolve_mentions
from tracecause.documents.markdown import parse_document
from tracecause.evaluation.metrics import rate

from .prepare import ROOT, digest, write_json


def main() -> None:
    path = ROOT / "data/runbooks/annotations.json"
    data = json.loads(path.read_bytes())
    services = tuple(ServiceIdentity(**item) for item in data["services"])
    results = []
    for index, case in enumerate(data["cases"]):
        header = {
            "source_id": f"evaluation-link-{index}",
            "version": "1.0",
            "title": "Mention review",
            "origin": "authored_demo",
            "document_type": "runbook",
            "published_at": "2025-12-01T00:00:00Z",
            "license": "Apache-2.0",
            "attribution": "TraceCause authored evaluation annotation",
        }
        if "curated_alias" in case:
            header["curated_service_refs"] = [case["curated_alias"]]
        source = ("---\n" + yaml.safe_dump(header) + "---\n# Mention\n" + case["text"]).encode()
        document = parse_document(source)
        predictions = [
            item for item in resolve_mentions(document, services) if item.mention == case["mention"]
        ]
        if len(predictions) > 1:
            raise ValueError("annotation has multiple matching mention predictions")
        predicted_status = predictions[0].status if predictions else "no_link"
        predicted_service = predictions[0].service_key if predictions else None
        results.append(
            {
                "case": index,
                "text": case["text"],
                "mention": case["mention"],
                "predicted_status": predicted_status,
                "predicted_service": predicted_service,
                "expected_status": case["expected_status"],
                "expected_service": case["expected_service_key"],
                "exact": predicted_status == case["expected_status"]
                and predicted_service == case["expected_service_key"],
            }
        )
    predicted = [row for row in results if row["predicted_status"] == "accepted"]
    expected = [row for row in results if row["expected_status"] == "accepted"]
    true_positive = sum(row["exact"] for row in predicted)
    result = {
        "status": "authored_annotation_diagnostic_complete",
        "origin": "authored_demo",
        "review_origin": "ai",
        "reviewer": "Codex (implementation author)",
        "independent_human_review": False,
        "annotations_sha256": digest(path),
        "review_rationale": (
            "Reviewed all twelve texts and service identities: unqualified payment is ambiguous "
            "between demo and staging; the curated pay-gateway alias selects demo payment; "
            "unique exact names resolve; combined labels and nonmatching nouns do not; "
            "the plural payments suggestion remains unresolved."
        ),
        "cases": len(results),
        "status_counts": dict(Counter(row["predicted_status"] for row in results)),
        "exact_outcomes": rate(sum(row["exact"] for row in results), len(results)).__dict__,
        "accepted_precision": rate(true_positive, len(predicted)).__dict__,
        "accepted_recall": rate(true_positive, len(expected)).__dict__,
        "limitation": ("Twelve authored regression cases; not a held-out corpus-level estimate."),
        "results": results,
    }
    write_json(ROOT / "reports/phase13-linking.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "results"}))


if __name__ == "__main__":
    main()
