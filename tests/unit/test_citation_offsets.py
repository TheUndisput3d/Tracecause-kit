"""Artifact byte pointers and fail-closed scope/hash boundaries."""

import json
from dataclasses import replace
from uuid import UUID

import pytest

from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.storage.artifacts import ArtifactError, ArtifactRepository


def fixture(tmp_path):
    artifacts = ArtifactRepository(tmp_path)
    candidate = EvidenceCandidate(
        "observation:sample",
        "observation",
        "text",
        (),
        {},
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
        100,
    )
    row = {
        "workspace_id": str(candidate.workspace_id),
        "episode_id": str(candidate.episode_id),
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "source_record_offset": 999,
        "end_ns": "90",
        "available_at": None,
        "text": "Unicode λ",
    }
    first = {**row, "span_id": "c" * 16}
    prefix = (json.dumps(first, ensure_ascii=False) + "\n").encode()
    descriptor = artifacts.put_bytes(prefix + (json.dumps(row) + "\n").encode())
    reference = {**row, "artifact_hash": descriptor.content_hash}
    return artifacts, candidate, reference, len(prefix)


def test_byte_offset_is_in_named_artifact(tmp_path):
    artifacts, candidate, reference, expected = fixture(tmp_path)
    resolver = SpanLocatorResolver(artifacts)
    assert resolver.offset(candidate, reference) == expected != reference["source_record_offset"]
    line = artifacts.get_bytes(reference["artifact_hash"])[expected:].splitlines()[0]
    assert json.loads(line)["span_id"] == reference["span_id"]


@pytest.mark.parametrize("change", ["scope", "episode", "future", "span", "offset", "available"])
def test_locator_rejects_invalid_source(tmp_path, change):
    artifacts, candidate, reference, _ = fixture(tmp_path)
    if change == "scope":
        candidate = replace(candidate, workspace_id=UUID(int=9))
    if change == "episode":
        candidate = replace(candidate, episode_id=UUID(int=9))
    if change == "future":
        candidate = replace(candidate, as_of_ns=80)
    if change == "span":
        reference["span_id"] = "f" * 16
    if change == "offset":
        reference["source_record_offset"] = 0
    with pytest.raises(ValueError, match="citation span"):
        SpanLocatorResolver(artifacts).offset(
            candidate, reference, mode="available" if change == "available" else "event"
        )


def test_digest_is_checked_even_after_matching_record(tmp_path):
    artifacts, candidate, reference, _ = fixture(tmp_path)
    digest = reference["artifact_hash"]
    path = tmp_path / "sha256" / digest[:2] / digest
    path.write_bytes(path.read_bytes().replace(b"Unicode", b"Changed"))
    with pytest.raises(ArtifactError, match="hash mismatch"):
        SpanLocatorResolver(artifacts).offset(candidate, reference)


@pytest.mark.parametrize(
    "available, succeeds",
    [("1970-01-01T00:00:00+00:00", True), ("1970-01-01T00:00:01+00:00", False)],
)
def test_availability_cutoff_uses_the_record(tmp_path, available, succeeds):
    artifacts, candidate, reference, expected = fixture(tmp_path)
    lines = [
        json.loads(line) for line in artifacts.get_bytes(reference["artifact_hash"]).splitlines()
    ]
    lines[-1]["available_at"] = available
    payload = b"".join((json.dumps(row) + "\n").encode() for row in lines)
    reference["artifact_hash"] = artifacts.put_bytes(payload).content_hash
    resolver = SpanLocatorResolver(artifacts)
    if succeeds:
        offset = resolver.offset(candidate, reference, mode="available")
        assert json.loads(payload[offset:].splitlines()[0])["available_at"] == available
    else:
        with pytest.raises(ValueError, match="not available"):
            resolver.offset(candidate, reference, mode="available")
