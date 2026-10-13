"""Export measured aggregates into the public report contract, without private rows.

Local exports remain unpublished. The API publication lifecycle is a later phase.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from tracecause.contracts.canonical import semantic_sha256
from tracecause.contracts.models import EvaluationReport

from .prepare import ROOT, digest, write_json
from .systems import SYSTEMS

RATES = (
    "answered_coverage",
    "answered_identifiable_top1_error",
    "healthy_hypothesis_rate",
    "ambiguous_hypothesis_rate",
    "exposed_hit1_identifiable",
    "citation_validity",
    "numeric_consistency",
)
RANKING_RATES = (
    "candidate_recall",
    "hit_at_1",
    "hit_at_3",
    "conditional_hit_at_1",
    "conditional_hit_at_3",
)


def make_report(
    measured: dict[str, Any],
    completion: dict[str, Any],
    *,
    split_hash: str,
    created_at: datetime,
) -> EvaluationReport:
    manifest = measured["manifest"]
    origin = manifest["origin"]
    if origin not in {"synthetic", "captured_demo"}:
        raise ValueError("unsupported report origin")
    expected = 120 if origin == "synthetic" else 24
    expected_status = (
        "synthetic_template_comparison_complete"
        if origin == "synthetic"
        else "captured_transfer_complete"
    )
    if (
        completion.get("episodes") != expected
        or completion.get("smoke")
        or completion.get("partial_selection")
        or completion.get("status") != expected_status
    ):
        raise ValueError("report requires the complete declared dataset")
    identity = manifest["identity"]
    if identity.get("generation") != "template":
        raise ValueError("this exporter only covers the measured template benchmark")
    metrics: dict[str, float | None] = {}

    def add_rate(prefix: str, value: dict[str, Any]) -> None:
        numerator, denominator = value["numerator"], value["denominator"]
        if not (0 <= numerator <= denominator):
            raise ValueError("invalid measured numerator or denominator")
        expected_value = numerator / denominator if denominator else None
        if value["value"] != expected_value:
            raise ValueError("measured rate disagrees with its denominator")
        metrics[prefix] = expected_value
        metrics[f"{prefix}.numerator"] = numerator
        metrics[f"{prefix}.denominator"] = denominator

    seen = set()
    for group in measured["groups"]:
        if group["track"] != "canonical" or group["category"] != "all":
            continue
        split, system = group["split"], group["variant"]
        if split not in {"train", "dev", "test"} or system not in SYSTEMS:
            continue
        if (split, system) in seen:
            raise ValueError("duplicate report aggregate")
        seen.add((split, system))
        prefix = f"canonical.{split}.{system}"
        rank = group["ranking"]
        metrics[f"{prefix}.episodes"] = rank["episodes"]
        metrics[f"{prefix}.identifiable_faults"] = rank["identifiable_faults"]
        metrics[f"{prefix}.mrr"] = rank["mrr"]
        for key in RANKING_RATES:
            add_rate(f"{prefix}.{key}", rank[key])
        for key in RATES:
            add_rate(f"{prefix}.{key}", group[key])
    if seen != {(split, system) for split in ("train", "dev", "test") for system in SYSTEMS}:
        raise ValueError("report lacks a system or split aggregate")
    for system in SYSTEMS:
        if (
            sum(
                metrics[f"canonical.{split}.{system}.episodes"] or 0
                for split in ("train", "dev", "test")
            )
            != expected
        ):
            raise ValueError("report episode totals differ from completion")
    config_hash = semantic_sha256(identity)
    dataset_hash = identity["prepared_index_sha256"]
    identifier = semantic_sha256(
        dict(
            origin=origin,
            dataset=dataset_hash,
            split=split_hash,
            config=config_hash,
            metrics=metrics,
        )
    )
    return EvaluationReport(
        report_id=uuid5(NAMESPACE_URL, f"tracecause:evaluation:{identifier}"),
        origin=origin,
        dataset_hash=dataset_hash,
        split_hash=split_hash,
        config_hash=config_hash,
        model_digest=identity["config"]["model_hash"],
        sample_size=expected,
        metrics=metrics,
        created_at=created_at,
        published_at=None,
    )


def export(benchmark: Path, output: Path) -> dict[str, Any]:
    measured = json.loads((benchmark / "metrics.json").read_bytes())
    completion = json.loads((benchmark / "completion.json").read_bytes())
    rows = json.loads((benchmark / "scored_predictions.json").read_bytes())
    assignments = {(row["episode_id"], row["split"], row["group_id"]) for row in rows}
    if len({row[0] for row in assignments}) != len(assignments):
        raise ValueError("episode occurs in multiple splits/groups")
    if len(assignments) != completion["episodes"] or len(rows) != completion["runs"]:
        raise ValueError("completion and scored prediction counts differ")
    report = make_report(
        measured,
        completion,
        split_hash=semantic_sha256({"assignments": sorted(assignments)}),
        created_at=datetime.now(UTC),
    )
    path = output / f"{report.report_id}.json"
    if path.exists():
        previous = EvaluationReport.model_validate_json(path.read_bytes())
        report = report.model_copy(update={"created_at": previous.created_at})
        if previous != report:
            raise ValueError("immutable contract report differs")
    write_json(path, report.model_dump(mode="json"))
    metadata = {
        "report_id": str(report.report_id),
        "origin": report.origin,
        "generation_origin": "template",
        "review_origin": "ai",
        "independent_human_review": False,
        "publication_status": "local_unpublished",
        "metrics_sha256": digest(benchmark / "metrics.json"),
        "completion_sha256": digest(benchmark / "completion.json"),
        "limitations": [
            "Controlled demonstration data, not production validation.",
            "AI review by the implementation assistant, not independent human review.",
            "Candidate ranking, exposed hypotheses and answer coverage are distinct.",
            "Null metrics have no eligible denominator; zero is not substituted.",
            "Five canonical systems only; full offline reports contain other diagnostics.",
            "Template generation; no local language-model quality is measured.",
        ],
    }
    write_json(output / f"{report.report_id}.metadata.json", metadata)
    return {
        "report_id": str(report.report_id),
        "sample_size": report.sample_size,
        "metrics": len(report.metrics),
        "publication_status": "local_unpublished",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / "reports/phase13-contract-reports"
    )
    args = parser.parse_args()
    print(json.dumps(export(args.benchmark_root, args.output_root)))


if __name__ == "__main__":
    main()
