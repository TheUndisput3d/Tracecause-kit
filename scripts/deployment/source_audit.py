"""Independently seek live citations and recompute a bundled demo observation."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

from tracecause.storage.artifacts import ArtifactRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:13000")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifacts = ArtifactRepository(args.artifacts.absolute())
    with httpx.Client(base_url=args.url, trust_env=False, timeout=20) as client:
        episode = client.get("/api/v1/episodes").json()["items"][0]
        scope = {
            "snapshot_id": episode["active_snapshot_id"],
            "as_of": episode["event_interval"]["end"],
            "temporal_mode": "event_time_replay",
        }
        views = client.get(f"/api/v1/episodes/{episode['episode_id']}/investigations").json()[
            "items"
        ]
        checked = []
        for view in views:
            result = client.get(f"/api/v1/investigations/{view['investigation_id']}").json()[
                "result"
            ]
            if result is None:
                continue
            for saved in result["evidence"]:
                response = client.get(
                    "/api/v1/evidence/" + saved["evidence_id"],
                    params={
                        **scope,
                        "snapshot_id": result["snapshot_id"],
                        "as_of": result["as_of"],
                    },
                )
                response.raise_for_status()
                evidence = response.json()
                locator = evidence["locator"]
                payload = artifacts.get_bytes(locator["source_hash"])
                if evidence["kind"] == "observation":
                    offset = locator["record_offset"]
                    assert offset == 0 or payload[offset - 1] == 10
                    row = json.loads(payload[offset:].split(b"\n", 1)[0])
                    assert (row["trace_id"], row["span_id"]) == (
                        locator["trace_id"],
                        locator["span_ids"][0],
                    )
                    assert row["workspace_id"] == evidence["workspace_id"]
                    assert row["episode_id"] == evidence["episode_id"]
                else:
                    assert (
                        payload.decode()[locator["start_char"] : locator["end_char"]]
                        == evidence["text"]
                    )
                checked.append(
                    {
                        "result": result["investigation_id"],
                        "evidence": evidence["evidence_id"],
                        "saved_version": result["provenance"].get("template_version"),
                        "saved_locator_equal": saved["locator"] == locator,
                    }
                )
        response = client.get(
            "/api/v1/evidence/observation:ed341fe0-8eac-5ecd-a1fe-c98f76fc9ca6", params=scope
        )
        response.raise_for_status()
        example = response.json()
        payload = artifacts.get_bytes(example["locator"]["source_hash"])
        rows = [json.loads(line) for line in payload.splitlines()]
        assert len({(row["trace_id"], row["span_id"]) for row in rows}) == len(rows)
        original = Path("data/demo/episode/traces.jsonl").read_bytes()
        assert {row["source_hash"] for row in rows} == {hashlib.sha256(original).hexdigest()}
        normalized = next(
            row
            for row in rows
            if (
                row["trace_id"] == example["locator"]["trace_id"]
                and row["span_id"] == example["locator"]["span_ids"][0]
            )
        )
        original_offset = normalized["source_record_offset"]
        otlp = json.loads(original[original_offset:].split(b"\n", 1)[0])
        original_spans = [
            span
            for resource in otlp["resourceSpans"]
            for scope_row in resource["scopeSpans"]
            for span in scope_row["spans"]
        ]
        assert any(
            span["traceId"] == normalized["trace_id"] and span["spanId"] == normalized["span_id"]
            for span in original_spans
        )

        begin, end = [
            int(datetime.fromisoformat(example["interval"][key]).timestamp()) * 10**9
            for key in ("start", "end")
        ]
        selected = [
            row
            for row in rows
            if row["kind"] == "SERVER"
            and row["service_name"] == "payment"
            and begin <= int(row["end_ns"]) < end
        ]
        durations = [(int(row["end_ns"]) - int(row["start_ns"])) / 10**6 for row in selected]
        measured = {
            "request_count": len(selected),
            "error_count": sum(
                row["safe_attributes"].get("http.response.status_code", 0) >= 500
                for row in selected
            ),
            "p50_ms": float(np.quantile(durations, 0.5, method="linear")),
            "p95_ms": float(np.quantile(durations, 0.95, method="linear")),
        }
        expected = {metric["metric_name"]: metric["value"] for metric in example["metrics"]}
        assert measured == expected
        indexed = {(row["trace_id"], row["span_id"]): row for row in rows}
        witnesses = []
        for row in selected:
            parent = indexed.get((row["trace_id"], row["parent_span_id"]))
            if parent and parent["service_name"] == "checkout":
                assert parent["kind"] == "CLIENT"
                witnesses.append(
                    {"trace": row["trace_id"], "parent": parent["span_id"], "child": row["span_id"]}
                )
        assert witnesses
        report = {
            "review_origin": "ai",
            "independent_human_review": False,
            "citations_checked": checked,
            "normalized_artifact_sha256": hashlib.sha256(payload).hexdigest(),
            "source_sha256": rows[0]["source_hash"],
            "original_otlp_lineage_verified": True,
            "recomputed_example": measured,
            "checkout_payment_parent_witnesses": witnesses,
            "scope": "bundled synthetic demo; no causal inference or held-out reevaluation",
        }
        with args.output.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
        print(
            json.dumps(
                {"citations": len(checked), "recomputed": measured, "parent_pairs": len(witnesses)}
            )
        )


if __name__ == "__main__":
    main()
