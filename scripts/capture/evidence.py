"""Extract source-located span examples for offline label review; never approves labels."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding

from .minishop import PRIVATE, PUBLIC, ROOT, WORKSPACE_ID, dump
from .validation import timestamp_ns


def extract(public: Path, truth_path: Path) -> dict[str, Any]:
    truth = json.loads(truth_path.read_bytes())
    source = (public / "traces.jsonl").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != truth["source_sha256"]:
        raise ValueError("review evidence source hash differs")
    batch = normalize_otlp(
        BytesIO(source),
        encoding=InputEncoding.OTLP_JSONL_V1,
        workspace_id=UUID(WORKSPACE_ID),
        episode_id=UUID(truth["episode_id"]),
        expected_source_hash=digest,
        source_name="traces.jsonl",
    )
    if batch.fatal or batch.rejected_count:
        raise ValueError("invalid review source")
    lookup = {(span.trace_id, span.span_id): span for span in batch.spans}
    intervals = truth["actual_intervals"]
    ranges = {
        stage: (timestamp_ns(intervals[f"{stage}_start"]), timestamp_ns(intervals[f"{stage}_end"]))
        for stage in ("baseline", "intervention", "recovery")
    }
    examples: dict[tuple[str, str, bool], dict[str, Any]] = {}
    calls: Counter[str] = Counter()
    for span in batch.spans:
        if span.kind != "SERVER":
            continue
        parent = lookup.get((span.trace_id, span.parent_span_id))
        if parent and parent.kind == "CLIENT":
            calls[f"{parent.service_name}->{span.service_name}"] += 1
        stage = next(
            (key for key, (left, right) in ranges.items() if left <= int(span.start_ns) < right),
            None,
        )
        if stage is None:
            continue
        status = span.safe_attributes.get("http.status_code", 0)
        error = span.status == "ERROR" or isinstance(status, int) and status >= 500
        key = (stage, span.service_name or "unknown", error)
        if key in examples:
            continue
        examples[key] = {
            "stage": stage,
            "service": span.service_name,
            "http_status": status,
            "duration_ms": (int(span.end_ns) - int(span.start_ns)) / 1e6,
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "source_hash": digest,
            "source_record_offset": span.source_record_offset,
            "source_line_number": span.source_line_number,
            "source_resource_index": span.source_resource_index,
            "source_scope_index": span.source_scope_index,
            "source_span_index": span.source_span_index,
        }
    return {
        "episode_id": truth["episode_id"],
        "key": truth["key"],
        "source_hash": digest,
        "direct_call_counts": dict(sorted(calls.items())),
        "examples": list(examples.values()),
        "review_status": "evidence_only_not_approved",
    }


def main() -> None:
    audit_path = ROOT / "reports/capture/audit.json"
    audit_bytes = audit_path.read_bytes()
    audit = json.loads(audit_bytes)
    runs = [
        extract(PUBLIC / item["episode_id"], PRIVATE / item["episode_id"] / "truth.json")
        for item in audit["runs"]
        if item["split"] != "smoke"
    ]
    output = ROOT / "reports/capture/review_evidence.json"
    dump(output, {"audit_sha256": hashlib.sha256(audit_bytes).hexdigest(), "runs": runs})
    print(json.dumps({"episodes": len(runs), "output": str(output)}))


if __name__ == "__main__":
    main()
