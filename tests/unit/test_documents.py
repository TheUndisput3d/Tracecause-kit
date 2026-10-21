"""Exact-offset authored documents, entity links and assertion validation."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tracecause.documents.assertions import (
    AssertionError,
    DeterministicAssertionExtractor,
    validate_assertion,
)
from tracecause.documents.linking import ServiceIdentity, resolve_mentions
from tracecause.documents.markdown import (
    DocumentError,
    chunk_document,
    model_tokenizer,
    parse_document,
)

ROOT = Path(__file__).resolve().parents[2]


def _source(body: str, *, curated: str = "", doc_type: str = "runbook") -> bytes:
    return (
        "---\nsource_id: demo-test-01\nversion: '1.0'\ntitle: Unicode service check\n"
        f"origin: authored_demo\ndocument_type: {doc_type}\n"
        "published_at: 2025-12-01T00:00:00Z\nlicense: Apache-2.0\n"
        f"attribution: TraceCause authored test\n{curated}---\n{body}"
    ).encode()


def test_exact_unicode_offsets_and_heading_sections() -> None:
    document = parse_document(
        _source(
            "# Café 🙂\n\nKe बारे में payment.\n\n## Empty\n\n## Check\n\n"
            "**Check:** Inspect payment.\n"
        )
    )
    chunks = chunk_document(document, model_tokenizer())
    assert len(chunks) == 2
    assert all(
        document.source_text[item.start_char : item.end_char] == item.text for item in chunks
    )
    assert chunks[0].heading == "Café 🙂"
    assert chunks[1].heading == "Café 🙂 / Check"
    assert all(item.token_count <= 180 for item in chunks)


def test_long_paragraph_overlaps_and_fits_actual_tokenizer() -> None:
    document = parse_document(
        _source("# Notes\n" + ("checkout timeout payment inventory. " * 1000))
    )
    tokenizer = model_tokenizer()
    chunks = chunk_document(document, tokenizer)
    assert len(chunks) > 2
    assert all(item.token_count <= 180 for item in chunks)
    assert all(item.token_count <= 256 for item in chunks)
    assert all(
        document.source_text[item.start_char : item.end_char] == item.text for item in chunks
    )
    assert all(
        right.start_char < left.end_char for left, right in zip(chunks, chunks[1:], strict=False)
    )


def test_header_invalid_predicate_and_quote_offsets() -> None:
    with pytest.raises(DocumentError):
        parse_document(_source("# Empty\n\nBody", curated="unknown: ignored\n"))
    document = parse_document(_source("# Note\n\n**Check:** Inspect payment.\n"))
    services = (ServiceIdentity("payment-key", "minishop", "demo", "payment"),)
    links = resolve_mentions(document, services)
    assertion = DeterministicAssertionExtractor().extract(document, links)[0]
    with pytest.raises(AssertionError, match="offset"):
        validate_assertion(
            document,
            replace(assertion, start_char=assertion.start_char + 1),
            {"payment-key"},
        )
    with pytest.raises(AssertionError, match="predicate"):
        validate_assertion(document, replace(assertion, predicate="CAUSES"), {"payment-key"})
    with pytest.raises(AssertionError, match="historical"):
        validate_assertion(
            document,
            replace(assertion, predicate="REPORTS_HISTORICAL_CAUSE"),
            {"payment-key"},
        )


def test_entity_link_annotation_set() -> None:
    annotated = json.loads((ROOT / "data/runbooks/annotations.json").read_text())
    services = tuple(ServiceIdentity(**item) for item in annotated["services"])
    correct = 0
    for case in annotated["cases"]:
        curated = ""
        if "curated_alias" in case:
            rule = case["curated_alias"]
            curated = (
                "curated_service_refs:\n"
                f"  - namespace: {rule['namespace']}\n"
                f"    environment: {rule['environment']}\n"
                f"    name: {rule['name']}\n"
                f"    alias: {rule['alias']}\n"
            )
        document = parse_document(_source("# Note\n" + case["text"], curated=curated))
        links = resolve_mentions(document, services)
        matched = [item for item in links if item.mention == case["mention"]]
        if case["expected_status"] == "no_link":
            assert not matched, case
        else:
            assert len(matched) == 1, case
            assert matched[0].status == case["expected_status"], case
            assert matched[0].service_key == case["expected_service_key"], case
            correct += 1
    assert correct == 9


def test_demo_corpus_is_frozen_and_future_notes_are_identified() -> None:
    root = ROOT / "data/runbooks/demo"
    manifest = json.loads((root / "manifest.json").read_text())
    assert len(manifest["documents"]) == 24
    docs = [
        parse_document((root / f"{row['source_id']}.md").read_bytes())
        for row in manifest["documents"]
    ]
    assert sum(doc.header.document_type == "runbook" for doc in docs) == 18
    assert sum(doc.header.document_type == "historical_note" for doc in docs) == 6
    assert all(doc.header.origin == "authored_demo" for doc in docs)
