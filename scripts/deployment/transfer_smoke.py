"""Upload one predeclared captured development case and verify deployed v4 inference."""

import argparse
import csv
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from scripts.deployment.full_lifecycle import operator_token


def wait_job(client: httpx.Client, identifier: str, *, investigation: bool = False) -> dict:
    route = "/api/v1/investigations/" if investigation else "/api/v1/jobs/"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        response = client.get(route + identifier)
        response.raise_for_status()
        value = response.json()
        if value["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return value
        time.sleep(1)
    raise RuntimeError("captured Docker verification deadline exceeded")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    with Path("reports/capture/ai_review_sheet.csv").open(newline="") as source:
        row = next(row for row in csv.DictReader(source) if row["key"] == "catalog-01")
    assert row["split"] == "dev" and row["identifiable"] == "yes"
    root = Path("data/captured") / row["episode_id"]
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["episode_id"] = str(uuid4())
    manifest["display_name"] = "Captured development verification (v4)"
    prepared = json.loads(
        (Path("reports/phase13-captured-prepared-v1") / (row["episode_id"] + ".json")).read_bytes()
    )
    from tracecause.investigation.templates import ns_datetime

    cutoff = ns_datetime(prepared["as_of_ns"]).isoformat()
    headers = {"Authorization": "Bearer " + operator_token(), "Idempotency-Key": str(uuid4())}
    files = [
        ("files", (s["logical_name"], (root / s["logical_name"]).read_bytes(), "application/json"))
        for s in manifest["sources"]
    ]
    with httpx.Client(base_url="http://127.0.0.1:14000", trust_env=False, timeout=60) as client:
        response = client.post(
            "/api/v1/imports", data={"manifest": json.dumps(manifest)}, files=files, headers=headers
        )
        response.raise_for_status()
        job = wait_job(client, response.json()["job_id"])
        (args.output / "import.json").write_text(json.dumps(job, indent=2) + "\n")
        assert job["status"] == "SUCCEEDED", job
        scope = {
            "episode_id": manifest["episode_id"],
            "snapshot_id": job["result_id"],
            "as_of": cutoff,
            "temporal_mode": "event_time_replay",
        }
        graph = client.get("/api/v1/graph", params=scope)
        graph.raise_for_status()
        names = {node["service_id"]: node["display_name"] for node in graph.json()["nodes"]}
        symptom = next(key for key, name in names.items() if name == "storefront")
        response = client.post(
            "/api/v1/investigations",
            json={
                **scope,
                "question": "Which service should be checked first?",
                "symptom_service_id": symptom,
                "retrieval_mode": "GRAPH",
                "ranking_mode": "learned",
                "generation_mode": "ollama",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        response.raise_for_status()
        result = wait_job(client, response.json()["investigation_id"], investigation=True)
        (args.output / "investigation.json").write_text(json.dumps(result, indent=2) + "\n")
        assert result["status"] == "SUCCEEDED", result
        report = result["result"]
        assert report["ranking_mode"] == "learned"
        assert report["provenance"]["feature_schema_version"].endswith(
            "candidate-features-v4/candidate-logistic-v4"
        )
        assert report["narrative"]["status"] == "supported_hypotheses"
        assert report["generation_origin"] == "ollama" and report["fallback_reason"] is None
        top = names[report["ranked_scores"][0]["service_id"]]
        assert top in row["accepted_targets"].split(";"), top
        for item in report["evidence"]:
            opened = client.get("/api/v1/evidence/" + item["evidence_id"], params=scope)
            opened.raise_for_status()
            assert opened.json()["text"] == item["text"]
        summary = {
            "passed": True,
            "origin": "captured_demo",
            "split": "development",
            "source_key": row["key"],
            "top_service": top,
            "scope": scope,
            "model": report["provenance"]["feature_schema_version"],
            "generation_origin": report["generation_origin"],
            "usage": report["usage"],
            "citations": len(report["evidence"]),
            "timings_ms": report["timings_ms"],
        }
        (args.output / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
