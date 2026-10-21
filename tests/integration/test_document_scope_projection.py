"""Real PostgreSQL source admission preserves legacy and explicit scope policies."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.integration.test_persistence_jobs import (  # noqa: F401
    connection_url,
    factory,
    test_database_name,
)
from tests.unit.test_documents import _source

from tracecause.documents.linking import ServiceIdentity
from tracecause.documents.markdown import parse_document
from tracecause.documents.persistence import ingest_documents
from tracecause.documents.projection import DocumentProjectionError, load_document_material
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import DocumentRow


def test_exact_source_scope_and_pinned_membership(factory, tmp_path: Path) -> None:  # noqa: F811
    artifacts = ArtifactRepository(tmp_path / "artifacts")
    inputs = []
    ids = {}
    for label, namespace, environment in (
        ("unscoped", None, None),
        ("matching", "parcel", "test"),
        ("other", "minishop", "demo"),
    ):
        scope = (
            f"service_scope:\n  namespace: {namespace}\n  environment: {environment}\n"
            if namespace is not None
            else ""
        )
        raw = _source("# Guide\n**Check:** Inspect checkout.\n", curated=scope)
        source_id = f"scope-doc-{label}"
        raw = raw.replace(b"demo-test-01", source_id.encode())
        path = tmp_path / f"{source_id}.md"
        path.write_bytes(raw)
        inputs.append((path, hashlib.sha256(raw).hexdigest()))
        ids[label] = parse_document(raw).document_id
    imported = ingest_documents(factory, artifacts, tuple(inputs))
    assert imported.documents == 3
    services = (ServiceIdentity("parcel-checkout", "parcel", "test", "checkout"),)
    cutoff = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 1_000_000_000
    with factory() as session:
        explicit = load_document_material(
            session,
            artifacts,
            DEFAULT_WORKSPACE_ID,
            services,
            cutoff,
            require_explicit_scope=True,
        )
        assert {row["source_id"] for row in explicit.documents} == {"scope-doc-matching"}
        assert {row["service_key"] for row in explicit.chunk_service_links} == {"parcel-checkout"}
        assert {row["service_key"] for row in explicit.assertion_service_links} == {
            "parcel-checkout"
        }
        legacy = load_document_material(session, artifacts, DEFAULT_WORKSPACE_ID, services, cutoff)
        assert {row["source_id"] for row in legacy.documents} == {
            "scope-doc-matching",
            "scope-doc-unscoped",
        }
        pinned = load_document_material(
            session, artifacts, DEFAULT_WORKSPACE_ID, services, cutoff, (ids["unscoped"],)
        )
        assert len(pinned.documents) == 1
        assert pinned.documents[0]["source_id"] == "scope-doc-unscoped"
        assert pinned.chunk_service_links[0]["service_key"] == "parcel-checkout"
        with pytest.raises(DocumentProjectionError, match="pinned document"):
            load_document_material(
                session,
                artifacts,
                DEFAULT_WORKSPACE_ID,
                services,
                cutoff,
                (ids["unscoped"],),
                require_explicit_scope=True,
            )
        rows = session.scalars(select(DocumentRow)).all()
        metadata = {row.source_id: row.metadata_json for row in rows}
        assert "service_scope" not in metadata["scope-doc-unscoped"]
        assert metadata["scope-doc-matching"]["service_scope"] == {
            "namespace": "parcel",
            "environment": "test",
        }
