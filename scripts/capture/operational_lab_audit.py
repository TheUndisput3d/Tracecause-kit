"""Recheck the retained operational lab source bytes without any running services."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.capture.minishop import dump, quality
from scripts.capture.operational_lab import ROOT, sha
from tracecause.contracts.models import EpisodeManifest
from tracecause.operational.models import OperationalBundle


def audit(root: Path) -> dict[str, Any]:
    index = json.loads((root / "index.json").read_text())
    episodes = []
    for entry in index["episodes"]:
        folder = root / "episodes" / entry["episode_id"]
        manifest = EpisodeManifest.model_validate_json((folder / "manifest.json").read_bytes())
        for source in manifest.sources:
            raw = (folder / source.logical_name).read_bytes()
            if sha(raw) != source.sha256 or len(raw) != source.bytes:
                raise ValueError("captured source integrity differs from its manifest")
        raw = (folder / "traces.jsonl").read_bytes()
        stats = quality(raw, manifest.episode_id)
        if stats["fatal"] or stats["missing_parents"] or stats["rejected_spans"]:
            raise ValueError("captured telemetry failed normalization or parent integrity")
        operations = OperationalBundle.model_validate_json(
            (folder / "operational.json").read_bytes()
        )
        events = Counter(event.kind for event in operations.events)
        for event in operations.events:
            if (
                not manifest.event_interval.start
                <= event.occurred_at
                <= event.available_at
                <= manifest.event_interval.end
            ):
                raise ValueError("operational event falls outside the actual episode interval")
            if event.kind == "deployment":
                digest = str(event.details["artifact_sha256"])
                if sha((root / "artifacts" / f"{digest}.py").read_bytes()) != digest:
                    raise ValueError("deployed source bytes do not match their receipt")
        rows = []
        buckets: dict[str, set[int]] = defaultdict(set)
        baseline = manifest.baseline_interval
        if baseline is None:
            raise ValueError("controlled episode has no declared baseline")
        for line in raw.splitlines():
            for resource in json.loads(line)["resourceSpans"]:
                attributes = {
                    item["key"]: item["value"]["stringValue"]
                    for item in resource["resource"]["attributes"]
                }
                service = attributes["service.name"]
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        rows.append((service, span))
                        started, ended = (
                            int(span["startTimeUnixNano"]),
                            int(span["endTimeUnixNano"]),
                        )
                        if not (
                            int(manifest.event_interval.start.timestamp() * 1e9)
                            <= started
                            <= ended
                            <= int(manifest.event_interval.end.timestamp() * 1e9)
                        ):
                            raise ValueError(
                                "measured span lies outside the actual episode interval"
                            )
                        if span["kind"] == 2 and int(
                            baseline.start.timestamp() * 1e9
                        ) <= ended < int(baseline.end.timestamp() * 1e9):
                            buckets[service].add(ended // 10_000_000_000)
        by_id = {(row[1]["traceId"], row[1]["spanId"]): row for row in rows}
        transfers: Counter[str] = Counter()
        for service, span in rows:
            parent = by_id.get((span["traceId"], span["parentSpanId"]))
            if span["kind"] == 5 and parent is not None and parent[1]["kind"] == 4:
                transfers[f"{parent[0]}→{service}"] += 1
        episodes.append(
            {
                **entry,
                "source_quality": stats,
                "event_kinds": dict(events),
                "baseline_server_buckets": {key: len(value) for key, value in buckets.items()},
                "producer_consumer_pairs": dict(transfers),
                "analysis_seconds": (
                    datetime.fromisoformat(entry["analysis_end"])
                    - datetime.fromisoformat(entry["analysis_start"])
                ).total_seconds(),
            }
        )
    return {
        "status": "PASS",
        "origin": "captured_demo",
        "episodes": episodes,
        "applications": sorted({entry["application_id"] for entry in episodes}),
        "topologies": sorted({entry["topology"] for entry in episodes}),
        "accepted_spans": sum(item["source_quality"]["accepted_spans"] for item in episodes),
        "operational_events": sum(item["operational_events"] for item in episodes),
        "source_index_sha256": sha((root / "index.json").read_bytes()),
        "interpretation": "Source integrity and workload coverage; no diagnosis accuracy claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "data/operational-lab")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/operational-lab/audit.json")
    args = parser.parse_args()
    result = audit(args.root)
    dump(args.output, result)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "applications",
                    "topologies",
                    "accepted_spans",
                    "operational_events",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
