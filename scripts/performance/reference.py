"""Measured real reference investigations and loopback HTTP reads; no accuracy claims."""

import argparse
import json
import math
import os
import platform
import resource
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import event

from scripts.dashboard.seed import ROOT, environment
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import InvestigationRequest
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID


def distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "n": len(values),
        "p50": statistics.median(values),
        "p95_nearest_rank": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "min": min(values),
        "max": max(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True)
    fixture = json.loads((ROOT / "reports/phase15-dashboard/fixture.json").read_bytes())[
        "episodes"
    ][0]
    config = environment()
    resources = open_resources(load_settings(ROOT / "configs/defaults.yaml", config))
    statements = 0
    rows = []
    try:
        assert resources.engine is not None

        def count(*args, **kwargs):
            nonlocal statements
            statements += 1

        event.listen(resources.engine, "before_cursor_execute", count)
        service = resources.investigations
        for mode in ("LEXICAL", "GRAPH"):
            for index in range(10):
                request = InvestigationRequest.model_validate(
                    {
                        "episode_id": fixture["episode_id"],
                        "snapshot_id": fixture["snapshot_id"],
                        "as_of": fixture["cutoff"],
                        "temporal_mode": "event_time_replay",
                        "question": f"Performance diagnostic {mode} {index} {uuid4()}",
                        "retrieval_mode": mode,
                        "ranking_mode": "heuristic",
                        "generation_mode": "template",
                    }
                )
                before = statements
                started = time.perf_counter()
                result = service.investigate(DEFAULT_WORKSPACE_ID, request)
                wall = (time.perf_counter() - started) * 1000
                used_sql = statements - before
                assert not result.is_replayed
                for evidence in result.evidence:
                    service.evidence(
                        DEFAULT_WORKSPACE_ID, result.investigation_id, evidence.evidence_id
                    )
                rows.append(
                    {
                        "mode": mode,
                        "index": index,
                        "cache": "first process call"
                        if index == 0
                        else "warm filesystem/embedding cache; fresh result",
                        "wall_ms": wall,
                        "stages_ms": result.timings_ms,
                        "sql_statements": used_sql,
                        "citations_resolved": len(result.evidence),
                        "fallback": result.fallback_reason,
                        "truncated": any("truncated" in w for w in result.warnings),
                    }
                )
        with httpx.Client(base_url="http://127.0.0.1:18080", timeout=15, trust_env=False) as client:
            endpoints = {
                "metadata": f"/api/v1/episodes/{fixture['episode_id']}",
                "graph": "/api/v1/graph",
            }
            reads = {}
            for name, url in endpoints.items():
                params = (
                    {
                        "episode_id": fixture["episode_id"],
                        "snapshot_id": fixture["snapshot_id"],
                        "as_of": fixture["cutoff"],
                        "temporal_mode": "event_time_replay",
                    }
                    if name == "graph"
                    else {}
                )

                def request_once(index, url=url, params=params, name=name):
                    started = time.perf_counter()
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    if name == "graph":
                        assert len(payload["nodes"]) <= 80 and len(payload["edges"]) <= 120
                    return (time.perf_counter() - started) * 1000

                with ThreadPoolExecutor(max_workers=4) as executor:
                    values = list(executor.map(request_once, range(40)))
                reads[name] = {
                    "concurrency": 4,
                    "milliseconds": distribution(values),
                    "samples": values,
                }
        report = {
            "kind": "synthetic_reference_performance",
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "platform": platform.platform(),
            "affinity": sorted(os.sched_getaffinity(0)),
            "meminfo": {
                line.split(":")[0]: line.split(":")[1].strip()
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith(("MemTotal:", "MemAvailable:"))
            },
            "episode_id": str(UUID(fixture["episode_id"])),
            "snapshot_id": fixture["snapshot_id"],
            "investigation_concurrency": 1,
            "http": reads,
            "investigations": rows,
            "warm_investigation_ms": {
                mode: distribution(
                    [r["wall_ms"] for r in rows if r["mode"] == mode and r["index"] > 0]
                )
                for mode in ("LEXICAL", "GRAPH")
            },
            "stages_ms": {
                mode: {
                    stage: distribution(
                        [
                            r["stages_ms"][stage]
                            for r in rows
                            if r["mode"] == mode and r["index"] > 0
                        ]
                    )
                    for stage in rows[0]["stages_ms"]
                }
                for mode in ("LEXICAL", "GRAPH")
            },
            "fallback_count": sum(r["fallback"] is not None for r in rows),
            "truncated_count": sum(r["truncated"] for r in rows),
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "errors": 0,
            "provider_calls": 0,
            "hardware_target_note": "Actual developer host, not an emulated4-vCPU/8GiB host.",
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"investigations", "http"}}, indent=2
            )
        )
    finally:
        resources.close()


if __name__ == "__main__":
    main()
