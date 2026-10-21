"""Verify the actual complete Docker stack, including queued local AI generation."""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

COMPOSE = ["docker", "compose", "-f", "compose.full.yaml"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:14000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    token = subprocess.check_output(
        [*COMPOSE, "exec", "-T", "api", "cat", "/run/secrets/operator_token"], text=True
    ).strip()
    with httpx.Client(base_url=args.url, trust_env=False, timeout=20) as client:
        config = client.get("/api/v1/config").json()
        assert config["mutations_enabled"] and not config["public_read_only"]
        assert set(config["generation_modes"]) == {"template", "ollama"}
        assert set(config["ranking_modes"]) == {"heuristic", "learned"}
        ready = client.get("/health/ready")
        ready.raise_for_status()
        assert not ready.json()["generation_degraded"]
        assert client.post("/api/v1/investigations", json={}).status_code in (401, 403)
        for path in ("/.env", "/data/gold/index.json", "/openapi.json", "/internal/metrics"):
            assert client.get(path).status_code == 404
        episode = client.get("/api/v1/episodes").json()["items"][0]
        scope = {
            "episode_id": episode["episode_id"],
            "snapshot_id": episode["active_snapshot_id"],
            "as_of": episode["event_interval"]["end"],
            "temporal_mode": "event_time_replay",
        }
        graph = client.get("/api/v1/graph", params=scope)
        graph.raise_for_status()
        assert graph.json()["nodes"] and graph.json()["edges"]
        results = []
        for retrieval, ranking, generation in (
            ("GRAPH", "heuristic", "template"),
            ("GRAPH", "learned", "template"),
            ("LEXICAL", "heuristic", "ollama"),
        ):
            key = "full-stack-" + str(uuid4())
            payload = {
                **scope,
                "question": "Describe the eligible measurements and their limitations. " + key,
                "symptom_service_id": None,
                "retrieval_mode": retrieval,
                "ranking_mode": ranking,
                "generation_mode": generation,
            }
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": key}
            response = client.post("/api/v1/investigations", json=payload, headers=headers)
            assert response.status_code == 202, response.text
            submitted = response.json()
            again = client.post("/api/v1/investigations", json=payload, headers=headers)
            assert again.json()["investigation_id"] == submitted["investigation_id"]
            started = time.monotonic()
            while time.monotonic() - started < 240:
                view = client.get("/api/v1/investigations/" + submitted["investigation_id"])
                view.raise_for_status()
                value = view.json()
                if value["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("full-stack investigation exceeded smoke deadline")
            name = f"{retrieval.lower()}-{ranking}-{generation}"
            (args.output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
            assert value["status"] == "SUCCEEDED", value.get("safe_error")
            result = value["result"]
            assert result["ranking_mode"] == ranking
            assert result["generation_origin"] == generation, result["fallback_reason"]
            if generation == "ollama":
                assert result["usage"]["prompt_tokens"] > 0 and result["usage"]["output_tokens"] > 0
            for item in result["evidence"]:
                opened = client.get("/api/v1/evidence/" + item["evidence_id"], params=scope)
                opened.raise_for_status()
                assert opened.json()["locator"] == item["locator"]
                assert opened.json()["text"] == item["text"]
            results.append(
                {
                    "mode": name,
                    "id": value["investigation_id"],
                    "status": result["narrative"]["status"],
                    "citations": len(result["evidence"]),
                    "seconds": time.monotonic() - started,
                    "usage": result["usage"],
                    "fallback": result["fallback_reason"],
                }
            )
        report = {
            "url": args.url,
            "scope": scope,
            "real_queued_investigations": results,
            "credentials_printed": False,
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
