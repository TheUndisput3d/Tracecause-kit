"""Validate projected assertion nodes and relationships against immutable source."""

from typing import Any

from tracecause.documents.assertions import DeterministicAssertionExtractor
from tracecause.documents.linking import EntityLink
from tracecause.documents.markdown import ParsedDocument
from tracecause.graph.errors import GraphReadError


def verified_assertions(
    parsed: ParsedDocument,
    links: tuple[EntityLink, ...],
    projected: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    extracted = DeterministicAssertionExtractor().extract(parsed, links)
    by_id = {row["assertion_id"]: row for row in projected}
    if len(by_id) != len(projected) or set(by_id) != {str(item.id) for item in extracted}:
        raise GraphReadError("graph assertion membership differs from source")
    result = []
    for item in extracted:
        row = by_id[str(item.id)]
        expected = {
            "assertion_id": str(item.id),
            "document_id": str(item.document_id),
            "predicate": item.predicate,
            "quote": item.quote,
            "start_char": item.start_char,
            "end_char": item.end_char,
            "extractor_version": item.extractor_version,
            "verification_status": item.verification_status,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise GraphReadError("graph assertion differs from exact source")
        if set(row["service_keys"]) != (
            {item.subject_service_key} if item.subject_service_key else set()
        ):
            raise GraphReadError("graph assertion subject differs from source")
        expected_chunks = {
            chunk["chunk_id"]
            for chunk in chunks
            if chunk["start_char"] < item.end_char and chunk["end_char"] > item.start_char
        }
        if set(row["chunk_ids"]) != expected_chunks:
            raise GraphReadError("graph assertion support differs from source")
        result.append({**expected, "service_key": item.subject_service_key})
    return tuple(result)
