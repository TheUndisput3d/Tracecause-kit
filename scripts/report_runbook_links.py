"""Offline deterministic link review for the frozen demo corpus."""

from __future__ import annotations

import json
from pathlib import Path

from tracecause.documents.linking import ServiceIdentity, resolve_mentions, review_report
from tracecause.documents.markdown import parse_document
from tracecause.ingestion.identity import AliasRegistry
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/runbooks/demo"
OUTPUT = ROOT / "reports/runbooks/link_review.json"


def main() -> None:
    resolver = AliasRegistry()
    services = tuple(
        ServiceIdentity(
            service_key=resolver.resolve(DEFAULT_WORKSPACE_ID, "minishop", "demo", name).key or "",
            namespace="minishop",
            environment="demo",
            name=name,
        )
        for name in ("storefront", "checkout", "payment", "catalog", "inventory")
    )
    reports = []
    for path in sorted(CORPUS.glob("*.md")):
        document = parse_document(path.read_bytes())
        reports.append(
            {
                "source_id": document.header.source_id,
                **review_report(resolve_mentions(document, services)),
            }
        )
    result = {
        "corpus_documents": len(reports),
        "accepted": sum(item["accepted"] for item in reports),
        "ambiguous": sum(item["ambiguous"] for item in reports),
        "unresolved": sum(item["unresolved"] for item in reports),
        "documents": reports,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "documents"}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
