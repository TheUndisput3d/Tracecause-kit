"""Resolve normalized JSONL byte positions without confusing original OTLP offsets."""

from __future__ import annotations

import calendar
import json
from datetime import datetime
from typing import Any

from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.storage.artifacts import ArtifactRepository


class SpanLocatorResolver:
    """Request-owned, one-artifact index; fully consume hash verification before use."""

    def __init__(self, artifacts: ArtifactRepository) -> None:
        self.artifacts = artifacts
        self._hash: str | None = None
        self._index: dict[tuple[str, str], tuple[int, str, str, int, int, str | None]] = {}

    def offset(
        self, candidate: EvidenceCandidate, reference: dict[str, Any], *, mode: str = "event"
    ) -> int:
        source_hash = reference["artifact_hash"]
        if self._hash != source_hash:
            index = {}
            offset = 0
            for line in self.artifacts.iter_lines(source_hash):
                row = json.loads(line)
                key = (row["trace_id"], row["span_id"])
                if key in index:
                    raise ValueError("duplicate span in citation artifact")
                index[key] = (
                    offset,
                    row["workspace_id"],
                    row["episode_id"],
                    row["source_record_offset"],
                    int(row["end_ns"]),
                    row["available_at"],
                )
                offset += len(line)
            # iter_lines checks the complete digest on exhaustion. Never cache a
            # partial index, including when the matching span was encountered early.
            self._hash, self._index = source_hash, index
        match = self._index.get((reference["trace_id"], reference["span_id"]))
        if match is None or (
            match[1] != str(candidate.workspace_id)
            or match[2] != str(candidate.episode_id)
            or match[3] != reference["source_record_offset"]
            or match[4] > candidate.as_of_ns
        ):
            raise ValueError("citation span is missing, outside scope or after cutoff")
        if mode == "available":
            available = datetime.fromisoformat(match[5]) if match[5] else None
            if available is None or (
                calendar.timegm(available.utctimetuple()) * 1_000_000_000
                + available.microsecond * 1000
                > candidate.as_of_ns
            ):
                raise ValueError("citation span was not available at cutoff")
        return match[0]
