"""Idempotent authored-document ingestion into PostgreSQL and artifact storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.documents.markdown import (
    CHUNKING_VERSION,
    DocumentError,
    chunk_document,
    model_tokenizer,
    parse_document,
)
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, StorageConflict
from tracecause.storage.schema import ChunkRow, DocumentRow, SourceArtifactRow, WorkspaceRow


@dataclass(frozen=True)
class CorpusImport:
    corpus_hash: str
    documents: int
    chunks: int
    reused_documents: int
    document_ids: tuple[UUID, ...]


def _validated_inputs(corpus_dir: Path) -> tuple[tuple[Path, str], ...]:
    if (
        not corpus_dir.is_dir()
        or "gold" in corpus_dir.resolve().parts
        or any(path.is_symlink() for path in (corpus_dir, *corpus_dir.parents))
    ):
        raise DocumentError("corpus directory is missing or unsafe")
    manifest_path = corpus_dir / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 128 * 1024:
        raise DocumentError("corpus manifest is unsafe")
    manifest = json.loads(manifest_path.read_text())
    rows = manifest.get("documents") if isinstance(manifest, dict) else None
    if (
        not isinstance(rows, list)
        or not 20 <= len(rows) <= 30
        or manifest.get("origin") != "authored_demo"
        or manifest.get("schema_version") != "1.0"
    ):
        raise DocumentError("corpus manifest is invalid")
    selected: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DocumentError("corpus entry is invalid")
        source_id, expected = row.get("source_id"), row.get("sha256")
        if (
            not isinstance(source_id, str)
            or not source_id.startswith("demo-doc-")
            or not source_id.removeprefix("demo-doc-").isdigit()
            or source_id in seen
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise DocumentError("corpus entry identity is invalid")
        seen.add(source_id)
        path = corpus_dir / f"{source_id}.md"
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row.get("bytes"):
            raise DocumentError("corpus source is missing or unsafe")
        selected.append((path, expected))
    if {path.name for path in corpus_dir.glob("*.md")} != {path.name for path, _ in selected}:
        raise DocumentError("unlisted corpus source exists")
    return tuple(selected)


def ingest_corpus(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    corpus_dir: Path,
    *,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> CorpusImport:
    return ingest_documents(
        session_factory, artifacts, _validated_inputs(corpus_dir), workspace_id=workspace_id
    )


def ingest_documents(
    session_factory: sessionmaker[Session],
    artifacts: ArtifactRepository,
    inputs: tuple[tuple[Path, str], ...],
    *,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> CorpusImport:
    """Ingest explicitly allowlisted, hash-bound authored Markdown sources."""
    if workspace_id != DEFAULT_WORKSPACE_ID:
        raise StorageConflict("workspace is outside configured scope")
    if not inputs or len(inputs) > 100:
        raise DocumentError("document input count outside bounds")
    tokenizer = model_tokenizer()
    documents = []
    all_chunks = []
    for path, expected_hash in inputs:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
            raise DocumentError("document input is unsafe")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise DocumentError("corpus source hash differs from manifest")
        parsed = parse_document(raw)
        if path.stem != parsed.header.source_id:
            raise DocumentError("document source ID differs from manifest")
        chunks = chunk_document(parsed, tokenizer)
        documents.append((parsed, raw))
        all_chunks.append(chunks)
    reused = 0
    with session_factory.begin() as session:
        if session.get(WorkspaceRow, workspace_id) is None:
            session.add(WorkspaceRow(id=workspace_id, name="default"))
            session.flush()
        for (parsed, raw), chunks in zip(documents, all_chunks, strict=True):
            descriptor = artifacts.put_bytes(raw)
            if descriptor.content_hash != parsed.source_hash:
                raise DocumentError("artifact hash differs from document")
            source = session.get(SourceArtifactRow, descriptor.content_hash)
            if source is None:
                session.add(
                    SourceArtifactRow(
                        content_hash=descriptor.content_hash,
                        workspace_id=workspace_id,
                        kind="authored_document",
                        byte_size=descriptor.byte_size,
                        relative_storage_key=descriptor.relative_key,
                        schema_version="1.0",
                        received_at=datetime.now(UTC),
                        redaction_version="authored-demo-v1",
                    )
                )
                session.flush()
            elif source.workspace_id != workspace_id or source.kind != "authored_document":
                raise StorageConflict("document artifact conflicts with scope or kind")
            old = session.scalar(
                select(DocumentRow).where(
                    DocumentRow.workspace_id == workspace_id,
                    DocumentRow.source_id == parsed.header.source_id,
                    DocumentRow.version == parsed.header.version,
                )
            )
            if old is not None:
                if (
                    old.id != parsed.document_id
                    or old.source_artifact_hash != descriptor.content_hash
                ):
                    raise StorageConflict("document source/version changed without a new version")
                reused += 1
                continue
            session.add(
                DocumentRow(
                    id=parsed.document_id,
                    source_id=parsed.header.source_id,
                    workspace_id=workspace_id,
                    source_artifact_hash=descriptor.content_hash,
                    version=parsed.header.version,
                    title=parsed.header.title,
                    published_at=parsed.header.published_at,
                    available_at=None,
                    document_type=parsed.header.document_type,
                    metadata_json={
                        "origin": parsed.header.origin,
                        "license": parsed.header.license,
                        "attribution": parsed.header.attribution,
                        "curated_service_refs": [
                            item.model_dump() for item in parsed.header.curated_service_refs
                        ],
                        **(
                            {"service_scope": parsed.header.service_scope.model_dump()}
                            if parsed.header.service_scope is not None
                            else {}
                        ),
                        "tokenizer_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
                    },
                )
            )
            session.flush()
            for chunk in chunks:
                session.add(
                    ChunkRow(
                        id=chunk.id,
                        document_id=chunk.document_id,
                        version=parsed.header.version,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                        token_count=chunk.token_count,
                        normalized_text_hash=chunk.normalized_text_hash,
                        embedding_artifact_hash=None,
                        chunking_version=CHUNKING_VERSION,
                    )
                )
    corpus_hash = hashlib.sha256(
        json.dumps([item.source_hash for item, _ in documents], separators=(",", ":")).encode()
    ).hexdigest()
    return CorpusImport(
        corpus_hash,
        len(documents),
        sum(map(len, all_chunks)),
        reused,
        tuple(item.document_id for item, _ in documents),
    )
