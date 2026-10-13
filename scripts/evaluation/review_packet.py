"""Build question-review inputs from verified public evidence; no automatic approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .prepare import ROOT, digest, write_json
from .systems import load_episode


def build(prepared_root: Path, draft_path: Path, output: Path) -> dict[str, Any]:
    prepared = json.loads((prepared_root / "index.json").read_bytes())
    draft = json.loads(draft_path.read_bytes())
    rows = {item["episode_id"]: item for item in prepared["episodes"]}
    if set(rows) != {item["episode_id"] for item in draft["questions"]}:
        raise ValueError("question packet needs every declared public episode")
    questions = []
    for question in draft["questions"]:
        row = rows[question["episode_id"]]
        episode = load_episode(prepared_root / f"{row['episode_id']}.json", row["sha256"])
        names = {key: name for name, key in episode.names.items()}
        obs = {item.id: item for item in episode.report.observations}
        services = []
        for signal in episode.report.signals:
            current = obs[signal.analysis_observation_id]
            baseline = [obs[key] for key in signal.baseline_observation_ids]
            services.append(
                {
                    "name": names.get(signal.service_key, signal.service_key),
                    "service_key": signal.service_key,
                    "latency_z": signal.latency_deviation_z,
                    "error_z": signal.error_excess_z,
                    "volume_z": signal.volume_deviation_z,
                    "flags": signal.flags,
                    "baseline_requests": sum(item.sample_count for item in baseline),
                    "baseline_errors": sum(item.error_count for item in baseline),
                    "analysis_requests": current.sample_count,
                    "analysis_errors": current.error_count,
                    "analysis_p95_ms": current.p95_ms,
                    "evidence_id": f"observation:{current.id}",
                    "baseline_evidence_ids": [f"observation:{item.id}" for item in baseline],
                    "sample_source_records": [
                        episode.report.source_locators[f"{trace}:{span}"]
                        for trace, span, _ in current.supports[:2]
                        if f"{trace}:{span}" in episode.report.source_locators
                    ],
                }
            )
        questions.append(
            {
                **question,
                "snapshot_id": str(episode.snapshot_id),
                "prepared_sha256": row["sha256"],
                "services": services,
                "calls": [
                    {
                        "caller": names.get(call.caller_service_key),
                        "callee": names.get(call.callee_service_key),
                        "evidence_ids": [
                            f"observation:{key}" for key in call.support_observation_ids
                        ],
                    }
                    for call in episode.calls
                ],
                "eligible_document_titles": sorted(
                    {
                        item.source_locator["title"]
                        for item in episode.corpus
                        if item.kind == "chunk"
                    }
                ),
                "packet_status": "evidence_only_not_approved",
            }
        )
    result = {
        "draft_sha256": digest(draft_path),
        "prepared_index_sha256": digest(prepared_root / "index.json"),
        "questions": questions,
        "status": "evidence_only_not_approved",
    }
    write_json(output, result)
    return {"questions": len(questions), "output": str(output)}


def main() -> None:
    print(
        json.dumps(
            build(
                ROOT / "reports/phase13-prepared-v2",
                ROOT / "reports/phase13-questions/draft_questions.json",
                ROOT / "reports/phase13-questions/review_inputs.json",
            )
        )
    )


if __name__ == "__main__":
    main()
