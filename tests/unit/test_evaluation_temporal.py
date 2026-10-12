"""The deliberately unrestricted offline arm must report future context explicitly."""

from uuid import uuid4

from scripts.evaluation.temporal_ablation import future_ids

from tracecause.retrieval.engine import EvidenceCandidate


def test_future_context_includes_unknown_publication_and_future_spans() -> None:
    workspace, episode, snapshot = uuid4(), uuid4(), uuid4()

    def item(key, kind, locator):
        return EvidenceCandidate(
            key, kind, "source text", (), locator, workspace, episode, snapshot, 100
        )

    corpus = (
        item("old", "observation", {"end_ns": 90}),
        item("future", "observation", {"end_ns": 110}),
        item("unknown", "chunk", {"published_at": None}),
        item("late_doc", "chunk", {"published_at": "2026-01-01T00:00:00Z"}),
    )
    assert future_ids(corpus, [x.evidence_id for x in corpus], 100) == [
        "future",
        "unknown",
        "late_doc",
    ]
    assert future_ids(corpus, ["old"], 100) == []
