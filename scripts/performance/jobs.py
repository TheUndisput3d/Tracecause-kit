"""Bounded real HTTP investigation queue load; template-only, four fresh jobs."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx

from scripts.performance.reference import distribution


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    fixture = json.loads(Path("reports/phase15-dashboard/fixture.json").read_text())["episodes"][0]
    token = Path("reports/phase15-dashboard/operator_token").read_text().strip()
    rows = []
    started = time.perf_counter()
    with httpx.Client(base_url="http://127.0.0.1:18080", timeout=15, trust_env=False) as client:

        def run(index: int) -> dict[str, object]:
            begin = time.perf_counter()
            request = dict(
                episode_id=fixture["episode_id"],
                snapshot_id=fixture["snapshot_id"],
                as_of=fixture["cutoff"],
                temporal_mode="event_time_replay",
                question=f"Queue performance {index} {uuid4()}",
                retrieval_mode="GRAPH",
                ranking_mode="heuristic",
                generation_mode="template",
            )
            posted = client.post(
                "/api/v1/investigations",
                json=request,
                headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())},
            )
            posted.raise_for_status()
            accepted = time.perf_counter()
            identifier = posted.json()["investigation_id"]
            while time.perf_counter() - begin < 60:
                response = client.get(f"/api/v1/investigations/{identifier}")
                response.raise_for_status()
                view = response.json()
                if view["status"] == "SUCCEEDED":
                    result = view["result"]
                    assert result["generation_origin"] == "template"
                    return {
                        "submit_ms": (accepted - begin) * 1000,
                        "completion_ms": (time.perf_counter() - begin) * 1000,
                        "investigation_id": identifier,
                        "stages_ms": result["timings_ms"],
                        "fallback": result["fallback_reason"],
                    }
                if view["status"] in {"FAILED", "CANCELLED"}:
                    raise RuntimeError("measured investigation failed")
                time.sleep(0.1)
            raise TimeoutError("measured investigation exceeded sixty seconds")

        with ThreadPoolExecutor(max_workers=2) as executor:
            rows = list(executor.map(run, range(4)))
    elapsed = time.perf_counter() - started
    report = {
        "kind": "real_http_template_investigations",
        "concurrency": 2,
        "worker_count": 1,
        "completed": len(rows),
        "elapsed_seconds": elapsed,
        "jobs_per_second": len(rows) / elapsed,
        "errors": 0,
        "provider_calls": 0,
        "rows": rows,
        "submit_ms": distribution([float(r["submit_ms"]) for r in rows]),
        "completion_ms": distribution([float(r["completion_ms"]) for r in rows]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
