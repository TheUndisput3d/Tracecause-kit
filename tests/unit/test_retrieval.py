"""Pure retrieval arithmetic and bounded traversal tests.

The deterministic test embedder below is a test double, not a semantic model.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from tracecause.documents.markdown import chunk_document, model_tokenizer, parse_document
from tracecause.graph.queries import EligibleCall
from tracecause.retrieval.embeddings import EmbeddingCache, EmbeddingError
from tracecause.retrieval.engine import (
    EvidenceCandidate,
    RetrievalRequest,
    dense_rank,
    expand_graph,
    lexical_rank,
    retrieve,
    rrf_fuse,
)

ROOT = Path(__file__).resolve().parents[2]


class TestOnlyEmbedder:
    dimension = 384
    version = "test-only-not-semantic"

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [tuple([1.0, *([0.0] * 383)]) for _ in texts]


def _candidate(text: str, key: str = "") -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=f"observation:{uuid4()}",
        kind="observation",
        text=text,
        service_keys=(key,) if key else (),
        source_locator={"test_only": True},
        workspace_id=uuid4(),
        episode_id=uuid4(),
        snapshot_id=uuid4(),
        as_of_ns=1,
    )


def test_rrf_rank_one_missing_modality_duplicate_and_ties() -> None:
    fused = rrf_fuse(
        {
            "dense": (("a", 0.9), ("a", 0.8), ("b", 0.7)),
            "lexical": (("b", 0.6), ("a", 0.5)),
        }
    )
    assert fused[0][0] == "a"
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1][1] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[0][2]["dense_rank"] == 1
    assert rrf_fuse({"dense": (("b", 0.1), ("a", 0.1))})[0][0] == "b"
    assert rrf_fuse({"dense": (), "lexical": ()}) == ()


def test_lexical_service_punctuation_and_malformed_metacharacters() -> None:
    first = _candidate("namespace.payment-api:v1 timeout")
    second = _candidate("catalog inventory")
    ranked = lexical_rank("namespace.payment-api:v1", (first, second))
    assert ranked[0][0] == first.evidence_id
    assert lexical_rank("+()[]{}*?", (first, second)) == ()
    assert lexical_rank("anything", ()) == ()


def test_dense_cache_is_content_addressed_and_checks_dimension(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "cache", TestOnlyEmbedder())
    first = _candidate("payment timeout")
    ranking, state = dense_rank("payment", (first,), cache)
    assert ranking[0][0] == first.evidence_id
    assert state == {"hits": 0, "misses": 2}
    _, second_state = dense_rank("payment", (first,), cache)
    assert second_state == {"hits": 2, "misses": 0}

    class WrongDimension(TestOnlyEmbedder):
        def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0,) for _ in texts]

    with pytest.raises(EmbeddingError, match="dimension"):
        EmbeddingCache(tmp_path / "wrong", WrongDimension()).encode(["text"])


def test_graph_cycles_fanout_paths_and_support_caps() -> None:
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    one, two, three = (uuid4() for _ in range(3))
    supported = {f"observation:{key}": _candidate("support") for key in (one, two, three)}
    calls = (
        EligibleCall(a, b, (one,)),
        EligibleCall(b, c, (two,)),
        EligibleCall(c, a, (three,)),
    )
    evidence, paths, truncated = expand_graph(calls, (a,), supported)
    assert not truncated
    assert f"observation:{one}" in evidence
    assert all(len(path["edges"]) <= 3 for path in paths)
    assert all(len(set(path["services"])) == len(path["services"]) for path in paths)
    assert any(step["traversal_direction"] == "reverse" for path in paths for step in path["edges"])
    fanout = tuple(EligibleCall(a, f"{index:064x}", (one,)) for index in range(1, 50))
    _, _, truncated = expand_graph(fanout, (a,), supported)
    assert truncated


def test_request_rejects_invalid_scope_time_and_question() -> None:
    scope = dict(workspace_id=uuid4(), episode_id=uuid4(), snapshot_id=uuid4())
    with pytest.raises(ValueError):
        RetrievalRequest(**scope, as_of_ns=0, mode="event", question="hello")
    with pytest.raises(ValueError):
        RetrievalRequest(**scope, as_of_ns=1, mode="event", question=" ")
    with pytest.raises(ValueError):
        RetrievalRequest(**scope, as_of_ns=1, mode="event", question="hello", top_k=61)


def test_draft_retrieval_judgments_resolve_to_authored_chunks() -> None:
    fixture = json.loads((ROOT / "data/retrieval/annotations.json").read_text())
    assert len(fixture["cases"]) >= 2
    tokenizer = model_tokenizer()
    known = {
        f"chunk:{chunk.id}"
        for path in (ROOT / "data/runbooks/demo").glob("demo-doc-*.md")
        for chunk in chunk_document(parse_document(path.read_bytes()), tokenizer)
    }
    for case in fixture["cases"]:
        assert case["query"]
        assert {item["label"] for item in case["judgments"]} >= {0, 1, 2}
        assert all(item["evidence_id"] in known for item in case["judgments"])


def test_empty_eligible_corpus_returns_no_citation_and_explicit_warning(tmp_path: Path) -> None:
    class EmptyReader:
        def eligible_observation_summaries(self, **_kwargs):
            return ()

        def eligible_document_chunks(self, **_kwargs):
            return ()

    from tracecause.storage.artifacts import ArtifactRepository

    request = RetrievalRequest(
        workspace_id=uuid4(),
        episode_id=uuid4(),
        snapshot_id=uuid4(),
        as_of_ns=1,
        mode="event",
        question="payment timeout",
    )
    result = retrieve(
        EmptyReader(), ArtifactRepository(tmp_path / "artifacts"), request, strategy="lexical"
    )
    assert result.candidates == ()
    assert result.warnings == ("no_eligible_documents", "no_eligible_evidence")


def test_internal_corpus_is_not_exported() -> None:
    from tracecause.retrieval.engine import RetrievalResult, result_jsonable

    selected = _candidate("selected")
    excluded = _candidate("unselected internal corpus text")
    result = RetrievalResult("lexical", (selected,), (), (), {}, (selected, excluded))
    exported = result_jsonable(result)
    assert "corpus" not in exported
    assert [row["evidence_id"] for row in exported["candidates"]] == [selected.evidence_id]
    assert excluded.text not in json.dumps(exported, default=str)
