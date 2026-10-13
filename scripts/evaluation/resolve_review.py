"""Resolve offline review judgments to actual scoped source bytes and timestamps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.storage.artifacts import ArtifactRepository

from .prepare import ROOT, digest, write_json
from .systems import load_episode


def resolve_candidate(
    item: EvidenceCandidate,
    artifacts: ArtifactRepository,
    span_cache: dict[str, dict[tuple[str, str], dict[str, Any]]],
) -> tuple[str, int]:
    locator = item.source_locator
    if item.kind == "chunk":
        source_hash = locator["source_hash"]
        text = artifacts.get_bytes(source_hash).decode("utf-8")
        left, right = locator["start_char"], locator["end_char"]
        if not 0 <= left < right <= len(text) or text[left:right] != item.text:
            raise ValueError("reviewed document quote does not resolve")
        return source_hash, 1
    supports = locator["supports"]
    if not supports:
        raise ValueError("reviewed observation has no source support")
    for reference in supports:
        source_hash = reference["artifact_hash"]
        if source_hash not in span_cache:
            rows = [json.loads(line) for line in artifacts.iter_lines(source_hash)]
            indexed = {(row["trace_id"], row["span_id"]): row for row in rows}
            if len(indexed) != len(rows):
                raise ValueError("duplicate source span in reviewed artifact")
            span_cache[source_hash] = indexed
        span = span_cache[source_hash].get((reference["trace_id"], reference["span_id"]))
        if span is None or (
            span["episode_id"] != str(item.episode_id)
            or span["workspace_id"] != str(item.workspace_id)
            or span["source_record_offset"] != reference["source_record_offset"]
            or int(span["end_ns"]) > item.as_of_ns
        ):
            raise ValueError("reviewed span reference is missing, out of scope or in the future")
    return supports[0]["artifact_hash"], len(supports)


def resolve(
    review_path: Path, prepared_root: Path, artifact_root: Path, output: Path
) -> dict[str, Any]:
    review = json.loads(review_path.read_bytes())
    if review["status"] not in {
        "human_attested_requires_evidence_resolution",
        "ai_attested_requires_evidence_resolution",
    }:
        raise ValueError("review has not passed attestation validation")
    prepared_index = json.loads((prepared_root / "index.json").read_bytes())
    prepared = {row["episode_id"]: row for row in prepared_index["episodes"]}
    artifacts = ArtifactRepository(artifact_root)
    span_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    checked, supports = 0, 0
    for question in review["questions"]:
        row = prepared[question["episode_id"]]
        episode = load_episode(prepared_root / f"{row['episode_id']}.json", row["sha256"])
        candidates = {item.evidence_id: item for item in episode.corpus}
        for judgment in question["evidence_judgments"]:
            item = candidates.get(judgment["evidence_id"])
            if item is None:
                raise ValueError("review judgment is outside eligible snapshot corpus")
            source_hash, count = resolve_candidate(item, artifacts, span_cache)
            if source_hash != judgment["source_hash"]:
                raise ValueError("review judgment source hash differs")
            checked += 1
            supports += count
        question["snapshot_id"] = str(episode.snapshot_id)
        question["prepared_sha256"] = row["sha256"]
        question["review_status"] = f"{review['review_origin']}_reviewed_source_resolved"
    result = {
        **review,
        "status": f"{review['review_origin']}_reviewed_source_resolved",
        "review_input_sha256": digest(review_path),
        "prepared_index_sha256": digest(prepared_root / "index.json"),
        "resolved_judgments": checked,
        "resolved_support_references": supports,
    }
    write_json(output, result)
    return {key: value for key, value in result.items() if key != "questions"}


def main() -> None:
    print(
        json.dumps(
            resolve(
                ROOT / "reports/phase13-questions/ai_reviewed_questions.json",
                ROOT / "reports/phase13-prepared-v2",
                ROOT / "reports/phase13-artifacts",
                ROOT / "reports/phase13-questions/ai_resolved_questions.json",
            )
        )
    )


if __name__ == "__main__":
    main()
