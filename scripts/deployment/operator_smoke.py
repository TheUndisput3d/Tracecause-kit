"""Create and resolve one actual template job through the local operator release."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = Path(".secrets/release/operator_token").read_text().strip()
    with httpx.Client(base_url="http://127.0.0.1:13000", trust_env=False, timeout=15) as client:
        assert client.get("/api/v1/config").json()["mutations_enabled"] is True
        episode = client.get("/api/v1/episodes").json()["items"][0]
        scope = dict(
            episode_id=episode["episode_id"],
            snapshot_id=episode["active_snapshot_id"],
            as_of=episode["event_interval"]["end"],
            temporal_mode="event_time_replay",
        )
        request = {
            **scope,
            "question": "Container worker verification " + str(uuid4()),
            "retrieval_mode": "GRAPH",
            "ranking_mode": "heuristic",
            "generation_mode": "template",
        }
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
        posted = client.post("/api/v1/investigations", json=request, headers=headers)
        assert posted.status_code == 202
        repeated = client.post("/api/v1/investigations", json=request, headers=headers)
        assert repeated.status_code == 202 and repeated.json()["job_id"] == posted.json()["job_id"]
        identifier = posted.json()["investigation_id"]
        started = time.perf_counter()
        while time.perf_counter() - started < 60:
            view = client.get(f"/api/v1/investigations/{identifier}").json()
            if view["status"] == "SUCCEEDED":
                break
            if view["status"] in {"FAILED", "CANCELLED"}:
                raise RuntimeError("container worker failed")
            time.sleep(0.2)
        else:
            raise TimeoutError("container worker did not finish")
        result = view["result"]
        assert (
            result["generation_origin"] == "template"
            and result["provenance"]["model_digest"] is None
        )
        for evidence in result["evidence"]:
            response = client.get("/api/v1/evidence/" + evidence["evidence_id"], params=scope)
            response.raise_for_status()
            assert response.json()["text"] == evidence["text"]
        report = {
            "investigation_id": identifier,
            "job_id": posted.json()["job_id"],
            "citations_resolved": len(result["evidence"]),
            "idempotent_submission": True,
            "elapsed_seconds": time.perf_counter() - started,
            "provider_calls": 0,
        }
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
