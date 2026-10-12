"""Source-resolved reviews must reject invented quotes and wrong-scope span references."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.evaluation.resolve_review import resolve_candidate

from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.storage.artifacts import ArtifactRepository


def test_reviewed_document_quote_is_exact(tmp_path: Path) -> None:
    artifacts = ArtifactRepository(tmp_path)
    source = b"before exact quote after"
    digest = artifacts.put_bytes(source).content_hash
    item = EvidenceCandidate(
        "chunk:example",
        "chunk",
        "exact quote",
        (),
        {"source_hash": digest, "start_char": 7, "end_char": 18},
        uuid4(),
        uuid4(),
        uuid4(),
        100,
    )
    assert resolve_candidate(item, artifacts, {}) == (digest, 1)
    with pytest.raises(ValueError, match="quote"):
        resolve_candidate(replace(item, text="invented quote"), artifacts, {})


@pytest.mark.parametrize("mutation", ["scope", "future", "offset", "missing", "duplicate"])
def test_reviewed_span_must_resolve_in_scope(tmp_path: Path, mutation: str) -> None:
    artifacts = ArtifactRepository(tmp_path)
    workspace, episode = uuid4(), uuid4()
    row = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "workspace_id": str(workspace),
        "episode_id": str(episode),
        "end_ns": 99,
        "source_record_offset": 10,
    }
    if mutation == "scope":
        row["episode_id"] = str(uuid4())
    elif mutation == "future":
        row["end_ns"] = 101
    elif mutation == "offset":
        row["source_record_offset"] = 11
    elif mutation == "missing":
        row["span_id"] = "c" * 16
    source = (json.dumps(row) + "\n") * (2 if mutation == "duplicate" else 1)
    digest = artifacts.put_bytes(source.encode()).content_hash
    item = EvidenceCandidate(
        "observation:example",
        "observation",
        "measured evidence",
        (),
        {
            "supports": [
                {
                    "artifact_hash": digest,
                    "trace_id": "a" * 32,
                    "span_id": "b" * 16,
                    "source_record_offset": 10,
                }
            ]
        },
        workspace,
        episode,
        uuid4(),
        100,
    )
    with pytest.raises(ValueError):
        resolve_candidate(item, artifacts, {})
