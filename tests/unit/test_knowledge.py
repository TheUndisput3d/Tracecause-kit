"""KG competency cases: directed paths, exact assertions, bounded display."""

from copy import deepcopy
from uuid import uuid4

import pytest
from tests.unit.test_documents import _source

from tracecause.contracts.models import GraphResponse
from tracecause.documents.assertions import DeterministicAssertionExtractor
from tracecause.documents.linking import ServiceIdentity, resolve_mentions
from tracecause.documents.markdown import chunk_document, model_tokenizer, parse_document
from tracecause.documents.verification import verified_assertions
from tracecause.graph.errors import GraphReadError
from tracecause.graph.knowledge import assemble_knowledge, reachable_checks


def graph(pairs, names="abcdef"):
    return GraphResponse(
        episode_id=uuid4(),
        snapshot_id=uuid4(),
        as_of="2026-01-01T00:00:00Z",
        temporal_mode="event_time_replay",
        truncated=False,
        warnings=[],
        nodes=[dict(service_id=n, service_key=n, display_name=n, quality_flags=[]) for n in names],
        edges=[
            dict(
                edge_id=f"{a}-{b}",
                source_service_id=a,
                target_service_id=b,
                relationship="CALLS",
                supporting_evidence_ids=[f"span:{uuid4()}"],
            )
            for a, b in pairs
        ],
    )


def chunks(names="abcdef"):
    return tuple(
        dict(
            source_id=n,
            start_char=0,
            end_char=100,
            title=n,
            chunk_id=str(uuid4()),
            document_id=str(uuid4()),
            text=f"Inspect {n}.",
            service_keys=[n],
            verified_assertions=[
                dict(
                    assertion_id=str(uuid4()),
                    predicate="SUGGESTS_CHECK",
                    quote=f"Inspect {n}.",
                    service_key=n,
                    start_char=0,
                    end_char=10,
                )
            ],
        )
        for n in names
    )


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([], "a"),
        ([("a", "b")], "ab"),
        ([("b", "a")], "a"),
        ([("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")], "abcd"),
        ([("a", "b"), ("a", "c"), ("a", "d")], "abcd"),
        ([("a", "b"), ("b", "a"), ("b", "c")], "abc"),
        ([("a", "b"), ("c", "d")], "ab"),
        ([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")], "abcd"),
    ],
)
def test_directed_competencies(pairs, expected):
    result = reachable_checks(graph(pairs), chunks(), "a")
    assert {c.service_id for c in result.checks} == set(expected)
    for check in result.checks:
        assert len(set(check.service_ids)) == len(check.service_ids)
        assert check.service_ids[0] == "a"
        assert all(edge.supporting_evidence_ids for edge in check.call_edges)
    if "d" in expected:
        assert len(next(c for c in result.checks if c.service_id == "d").call_edges) <= 3


def test_checks_exclude_ambiguous_noncheck_and_duplicate_assertions():
    rows = list(chunks("abc"))
    rows[1]["verified_assertions"][0]["service_key"] = None
    rows[2]["verified_assertions"][0]["predicate"] = "DESCRIBES_SYMPTOM"
    result = reachable_checks(graph([("a", "b"), ("a", "c")]), tuple(rows + rows), "a")
    assert len(result.checks) == 1
    with pytest.raises(ValueError, match="not visible"):
        reachable_checks(graph([]), (), "unknown")


def test_bounded_display_and_endpoint_integrity():
    rows = chunks("abcdefghijklmnopqrstuvwxyz")
    response = assemble_knowledge(graph([], "abcdefghijklmnopqrstuvwxyz"), (), rows)
    assert response.omitted_chunks == 14
    assert response.omitted_assertions == 14
    assert response.truncated
    assert {n.kind for n in response.nodes} == {"service", "document", "chunk", "assertion"}
    ids = {n.node_id for n in response.nodes}
    assert all(e.source in ids and e.target in ids and e.evidence_ids for e in response.edges)


def assertion_material():
    parsed = parse_document(_source("# Check\n\n**Check:** Inspect payment.\n"))
    links = resolve_mentions(parsed, (ServiceIdentity("payment", "minishop", "demo", "payment"),))
    item = DeterministicAssertionExtractor().extract(parsed, links)[0]
    parts = [
        dict(chunk_id=str(c.id), start_char=c.start_char, end_char=c.end_char)
        for c in chunk_document(parsed, model_tokenizer())
    ]
    row = dict(
        assertion_id=str(item.id),
        document_id=str(item.document_id),
        predicate=item.predicate,
        quote=item.quote,
        start_char=item.start_char,
        end_char=item.end_char,
        extractor_version=item.extractor_version,
        verification_status=item.verification_status,
        service_keys=["payment"],
        chunk_ids=[c["chunk_id"] for c in parts],
    )
    return parsed, links, [row], parts


def test_exact_projected_assertion_accepts_original():
    parsed, links, rows, parts = assertion_material()
    result = verified_assertions(parsed, links, rows, parts)
    assert result[0]["service_key"] == "payment"
    assert parsed.source_text[result[0]["start_char"] : result[0]["end_char"]] == result[0]["quote"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quote", "Run attacker code"),
        ("predicate", "CAUSES"),
        ("start_char", 0),
        ("service_keys", ["another-service"]),
        ("chunk_ids", []),
        ("document_id", str(uuid4())),
        ("verification_status", "unverified"),
    ],
)
def test_tampered_assertions_fail_closed(field, value):
    parsed, links, rows, parts = assertion_material()
    rows = deepcopy(rows)
    rows[0][field] = value
    with pytest.raises(GraphReadError):
        verified_assertions(parsed, links, rows, parts)


def test_missing_or_duplicate_assertion_fails_closed():
    parsed, links, rows, parts = assertion_material()
    for malformed in ([], rows + rows):
        with pytest.raises(GraphReadError):
            verified_assertions(parsed, links, malformed, parts)
