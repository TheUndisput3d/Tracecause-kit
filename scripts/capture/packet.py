"""Render private measured capture evidence for explicit source review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from .minishop import ROOT


def render_packet(audit: dict[str, Any], audit_sha256: str) -> str:
    lines = [
        "# Private MiniShop review packet",
        "",
        "Machine-generated measurements and target proposals; **not human-reviewed gold**.",
        "Keep this packet outside runtime inputs and public artifacts.",
        "",
        f"Audit SHA-256: `{audit_sha256}`. Dataset status: `{audit['status']}`.",
        f"Completed benchmark runs: {audit['benchmark_runs']} / 24.",
        "",
        "Read [the review guide](../../docs/CAPTURE_REVIEW.md), then fill only the reviewer",
        "columns in [review_sheet.csv](review_sheet.csv). Review raw spans and private",
        "control/load records before confirming a target. A propagated error is not",
        "proof that every affected service is a cause. Record uncertainty and disagreements.",
        "",
    ]
    for item in audit["runs"]:
        if item["split"] == "smoke":
            continue
        episode_id = str(UUID(item["episode_id"]))
        lines.extend(
            [
                f"## {item['key']} — {item['split']}",
                "",
                f"Episode: `{episode_id}`. Intended control: `{item['mode']}`.",
                f"Source SHA-256: `{item['source_sha256']}`.",
                "",
                f"[Public manifest](../../data/captured/{episode_id}/manifest.json) · "
                f"[OTLP source](../../data/captured/{episode_id}/traces.jsonl) · "
                f"[Private control/load](../../data/captured_private/{episode_id}/truth.json)",
                "",
                f"Automatic effect check: `{item['observable_effect']}`; {item['effect_note']}.",
                f"Quality issues: `{json.dumps(item['quality_issues'])}`.",
                "Proposed targets (unapproved): "
                f"`{';'.join(item['proposed_target_services']) or '(none)'}`.",
                "",
                "| Stage | Service | Server spans | HTTP 5xx | 5xx fraction | Median ms |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for stage in ("baseline", "intervention", "recovery"):
            window = item["windows"].get(stage, {})
            if not window:
                lines.append(f"| {stage} | **missing** | — | — | — | — |")
            for role, values in sorted(window.items()):
                count, errors = values["count"], values["http_5xx"]
                fraction = f"{errors / count:.4f}" if count else "undefined"
                lines.append(
                    f"| {stage} | {role} | {count} | {errors} | {fraction} | "
                    f"{values['median_duration_ms']:.3f} |"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "reports/capture/audit.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/capture/review_packet.md")
    args = parser.parse_args()
    source = args.audit.read_bytes()
    rendered = render_packet(json.loads(source), hashlib.sha256(source).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(json.dumps({"packet": str(args.output), "status": "review_packet_generated"}))


if __name__ == "__main__":
    main()
