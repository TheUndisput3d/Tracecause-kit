"""Authoritative document material for a new graph snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracecause.documents.assertions import DeterministicAssertionExtractor
from tracecause.documents.linking import (
    ServiceIdentity,
    document_matches_scope,
    resolve_mentions,
    review_report,
)
from tracecause.documents.markdown import chunk_document, model_tokenizer, parse_document
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.schema import ChunkRow, DocumentRow


class DocumentProjectionError(ValueError):
    """A stored document cannot be projected with exact source provenance."""


@dataclass(frozen=True)
class DocumentMaterial:
    documents: tuple[dict[str, Any], ...]
    chunks: tuple[dict[str, Any], ...]
    assertions: tuple[dict[str, Any], ...]
    document_chunk_links: tuple[dict[str, str], ...]
    chunk_service_links: tuple[dict[str, str], ...]
    assertion_chunk_links: tuple[dict[str, str], ...]
    assertion_service_links: tuple[dict[str, str], ...]
    review: tuple[dict[str, Any], ...]


def load_document_material(
    session: Session,
    artifacts: ArtifactRepository | None,
    workspace_id: UUID,
    services: tuple[ServiceIdentity, ...],
    document_cutoff_ns: int,
    document_ids: tuple[UUID, ...] | None = None,
    *,
    require_explicit_scope: bool = False,
) -> DocumentMaterial:
    cutoff = datetime.fromtimestamp(document_cutoff_ns / 1_000_000_000, UTC)
    rows = session.scalars(
        select(DocumentRow)
        .where(
            DocumentRow.workspace_id == workspace_id,
            DocumentRow.published_at.is_not(None),
            DocumentRow.published_at <= cutoff,
        )
        .order_by(DocumentRow.source_id, DocumentRow.version)
    ).all()
    if document_ids is not None:
        wanted = set(document_ids)
        rows = [row for row in rows if row.id in wanted]
        if {row.id for row in rows} != wanted:
            raise DocumentProjectionError("snapshot document membership is unavailable")
    if rows and artifacts is None:
        raise DocumentProjectionError("document artifact root is required")
    tokenizer = None
    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    document_chunk_links: list[dict[str, str]] = []
    chunk_service_links: list[dict[str, str]] = []
    assertion_chunk_links: list[dict[str, str]] = []
    assertion_service_links: list[dict[str, str]] = []
    reviews: list[dict[str, Any]] = []
    for row in rows:
        assert artifacts is not None
        if row.published_at is None:
            raise DocumentProjectionError("eligible document lacks publication time")
        raw = artifacts.get_bytes(row.source_artifact_hash)
        parsed = parse_document(raw)
        if (
            parsed.document_id != row.id
            or parsed.header.source_id != row.source_id
            or parsed.header.version != row.version
            or parsed.header.title != row.title
            or parsed.header.published_at != row.published_at
        ):
            raise DocumentProjectionError("stored document metadata differs from immutable source")
        if not document_matches_scope(
            parsed, services, require_explicit_scope=require_explicit_scope
        ):
            if document_ids is not None:
                raise DocumentProjectionError("pinned document violates its service scope policy")
            continue
        if tokenizer is None:
            tokenizer = model_tokenizer()
        built_chunks = chunk_document(parsed, tokenizer)
        stored_chunks = session.scalars(
            select(ChunkRow).where(ChunkRow.document_id == row.id).order_by(ChunkRow.start_char)
        ).all()
        if len(stored_chunks) != len(built_chunks) or any(
            stored.id != built.id
            or stored.start_char != built.start_char
            or stored.end_char != built.end_char
            or stored.normalized_text_hash != built.normalized_text_hash
            or stored.token_count != built.token_count
            for stored, built in zip(stored_chunks, built_chunks, strict=True)
        ):
            raise DocumentProjectionError("stored chunks differ from exact source")
        links = resolve_mentions(parsed, services)
        extracted = DeterministicAssertionExtractor().extract(parsed, links)
        reviews.append({"source_id": row.source_id, **review_report(links)})
        published_ns = (
            int(row.published_at.timestamp()) * 1_000_000_000 + row.published_at.microsecond * 1000
        )
        documents.append(
            {
                "source_id": row.source_id,
                "source_hash": row.source_artifact_hash,
                "document_id": str(row.id),
                "version": row.version,
                "title": row.title,
                "document_type": row.document_type,
                "published_at_ns": published_ns,
                "license": parsed.header.license,
                "attribution": parsed.header.attribution,
            }
        )
        for chunk in built_chunks:
            chunks.append(
                {
                    "source_id": row.source_id,
                    "source_hash": row.source_artifact_hash,
                    "document_id": str(row.id),
                    "chunk_id": str(chunk.id),
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "token_count": chunk.token_count,
                    "normalized_text_hash": chunk.normalized_text_hash,
                    "text": chunk.text,
                    "heading": chunk.heading,
                    "title": chunk.title,
                    "chunking_version": chunk.chunking_version,
                    "published_at_ns": published_ns,
                }
            )
            document_chunk_links.append({"document_id": str(row.id), "chunk_id": str(chunk.id)})
            for link in links:
                if (
                    link.status == "accepted"
                    and link.service_key is not None
                    and link.start_char >= chunk.start_char
                    and link.end_char <= chunk.end_char
                ):
                    chunk_service_links.append(
                        {
                            "chunk_id": str(chunk.id),
                            "service_key": link.service_key,
                        }
                    )
        for assertion in extracted:
            assertions.append(
                {
                    "source_id": row.source_id,
                    "document_id": str(row.id),
                    "assertion_id": str(assertion.id),
                    "predicate": assertion.predicate,
                    "quote": assertion.quote,
                    "start_char": assertion.start_char,
                    "end_char": assertion.end_char,
                    "extractor_version": assertion.extractor_version,
                    "verification_status": assertion.verification_status,
                    "published_at_ns": published_ns,
                }
            )
            containing = [
                chunk
                for chunk in built_chunks
                if chunk.start_char < assertion.end_char and chunk.end_char > assertion.start_char
            ]
            if not containing:
                raise DocumentProjectionError("assertion quote is outside every chunk")
            for chunk in containing:
                assertion_chunk_links.append(
                    {
                        "assertion_id": str(assertion.id),
                        "chunk_id": str(chunk.id),
                    }
                )
            if assertion.subject_service_key is not None:
                assertion_service_links.append(
                    {
                        "assertion_id": str(assertion.id),
                        "service_key": assertion.subject_service_key,
                    }
                )
    return DocumentMaterial(
        tuple(documents),
        tuple(chunks),
        tuple(assertions),
        tuple(document_chunk_links),
        tuple(chunk_service_links),
        tuple(assertion_chunk_links),
        tuple(assertion_service_links),
        tuple(reviews),
    )
