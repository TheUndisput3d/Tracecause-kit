"""Explicit application scope prevents cross-application document links."""

import pytest
from tests.unit.test_documents import _source

from tracecause.documents.linking import (
    ServiceIdentity,
    document_matches_scope,
    resolve_mentions,
)
from tracecause.documents.markdown import DocumentError, parse_document

PARCEL = ServiceIdentity("parcel-checkout", "parcel", "test", "checkout")
MINISHOP = ServiceIdentity("minishop-checkout", "minishop", "demo", "checkout")
OTHER_ENV = ServiceIdentity("parcel-prod-checkout", "parcel", "production", "checkout")
SCOPE = "service_scope:\n  namespace: parcel\n  environment: test\n"


def test_explicit_scope_disambiguates_same_short_name() -> None:
    document = parse_document(_source("# Guide\n**Check:** Inspect checkout.\n", curated=SCOPE))
    links = resolve_mentions(document, (MINISHOP, PARCEL, OTHER_ENV))
    assert len(links) == 1
    assert links[0].status == "accepted"
    assert links[0].service_key == PARCEL.service_key
    assert not resolve_mentions(document, (MINISHOP, OTHER_ENV))


def test_curated_alias_cannot_escape_explicit_scope() -> None:
    document = parse_document(
        _source(
            "# Guide\n**Check:** Inspect storefront.\n",
            curated=SCOPE
            + "curated_service_refs:\n"
            + "  - namespace: minishop\n    environment: demo\n"
            + "    name: checkout\n    alias: storefront\n",
        )
    )
    links = resolve_mentions(document, (MINISHOP, PARCEL))
    assert len(links) == 1
    assert links[0].status == "unresolved"
    assert links[0].service_key is None
    assert links[0].candidate_service_keys == ()


def test_legacy_unscoped_resolution_remains_unchanged() -> None:
    document = parse_document(_source("# Guide\n**Check:** Inspect checkout.\n"))
    for service in (MINISHOP, PARCEL):
        link = resolve_mentions(document, (service,))[0]
        assert link.status == "accepted"
        assert link.service_key == service.service_key
        assert link.alias_version == "demo-aliases-v1"
    link = resolve_mentions(document, (MINISHOP, PARCEL))[0]
    assert link.status == "ambiguous"
    assert link.service_key is None
    assert link.candidate_service_keys == (MINISHOP.service_key, PARCEL.service_key)


def test_document_admission_requires_exact_declared_namespace_and_environment() -> None:
    unscoped = parse_document(_source("# Guide\nCheck checkout.\n"))
    scoped = parse_document(_source("# Guide\nCheck checkout.\n", curated=SCOPE))
    assert document_matches_scope(unscoped, (PARCEL,))
    assert not document_matches_scope(unscoped, (PARCEL,), require_explicit_scope=True)
    assert document_matches_scope(scoped, (PARCEL,), require_explicit_scope=True)
    assert not document_matches_scope(scoped, (MINISHOP, OTHER_ENV))
    assert not document_matches_scope(scoped, ())


@pytest.mark.parametrize(
    "scope",
    [
        "service_scope: parcel\n",
        "service_scope:\n  namespace: parcel\n",
        "service_scope:\n  namespace: ''\n  environment: test\n",
        SCOPE + "  inferred: true\n",
    ],
)
def test_scope_contract_rejects_incomplete_or_unknown_fields(scope: str) -> None:
    with pytest.raises(DocumentError, match="header"):
        parse_document(_source("# Guide\nCheck checkout.\n", curated=scope))
