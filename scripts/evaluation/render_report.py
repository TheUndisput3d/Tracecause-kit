"""Generate the checked-in evaluation checkpoint from measured local artifacts."""

from __future__ import annotations

import json
from typing import Any

from .prepare import ROOT, digest


def captured_section(metrics: dict[str, Any], completion: dict[str, Any]) -> list[str]:
    if completion.get("status") != "captured_transfer_complete" or completion.get("episodes") != 24:
        raise ValueError("captured report requires the complete declared benchmark")

    def fraction(value: dict[str, Any]) -> str:
        if not value["denominator"]:
            return "undefined (n=0)"
        return f"{value['numerator']}/{value['denominator']}"

    lines = [
        "## Captured transfer (template generation)",
        "",
        f"The full captured comparison contains {completion['episodes']} episodes and "
        f"{completion['runs']} predictions. The synthetic-trained model and rejection "
        "threshold were frozen; no captured fitting or tuning was performed.",
        "",
        "| Split | System | Identifiable | Candidate Hit@1 | Exposed Hit@1 | Answered/all |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for group in metrics["groups"]:
        if group["track"] != "canonical" or group["category"] != "all":
            continue
        ranking = group["ranking"]
        lines.append(
            f"| {group['split']} | {group['variant']} | {ranking['identifiable_faults']} | "
            f"{fraction(ranking['hit_at_1'])} | {fraction(group['exposed_hit1_identifiable'])} | "
            f"{fraction(group['answered_coverage'])} |"
        )
    lines += [
        "",
        "The test split consists of five ambiguous caller-timeout cases and one healthy "
        "control. Unique-target ranking accuracy is undefined for this split. Coverage "
        "measures answers, not correctness; answering an ambiguous case is not a success. "
        "The artifact reports healthy/ambiguous hypothesis rates and paired episode "
        "coverage intervals. Modes are unevenly distributed across splits by the frozen "
        "capture schedule; differences between splits cannot isolate generalization.",
        "",
        "Captured retrieval relevance has no graded annotations and is not scored. "
        "Citation resolution and numeric consistency do not establish semantic support "
        "or causality. These controlled demo results do not validate production use.",
        "",
    ]
    return lines


def main() -> None:
    metrics_path = ROOT / "reports/phase13-benchmark-v1/metrics.json"
    metrics = json.loads(metrics_path.read_bytes())
    completion = json.loads((metrics_path.parent / "completion.json").read_bytes())
    temporal_path = ROOT / "reports/phase13-temporal-ablation-v1/summary.json"
    temporal = json.loads(temporal_path.read_bytes())
    review_path = ROOT / "reports/phase13-claim-review/validated_summary.json"
    review = json.loads(review_path.read_bytes())
    graph_path = ROOT / "reports/phase13-graph-projection-v1/results.json"
    graph = json.loads(graph_path.read_bytes())["summary"]
    pairs_path = ROOT / "reports/phase13-captured-pairs/summary.json"
    pairs = json.loads(pairs_path.read_bytes())
    captured_root = ROOT / "reports/phase13-captured-benchmark-v1"
    captured_complete = (captured_root / "completion.json").exists()
    status = (
        "Synthetic and captured template comparisons are complete. "
        if captured_complete
        else "Synthetic template results are complete; the full captured track is collecting. "
    )
    lines = [
        "# Evaluation checkpoint",
        "",
        "Generated from actual artifacts by `python -m scripts.evaluation.render_report`.",
        "",
        status + "Reviews were performed by the "
        "implementation assistant under user authorization, with no independent human review.",
        "",
        f"The synthetic comparison contains {completion['episodes']} episodes and "
        f"{completion['runs']} predictions (70 train /25 development /25 test). "
        "Two tracks use reviewed questions and a common diagnostic query. "
        "The table below is the canonical test track only.",
        "",
        "| System | Identifiable episodes | Candidate Hit@1 | Exposed Hit@1 | Answered/all |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in metrics["groups"]:
        if (group["track"], group["split"], group["category"]) != ("canonical", "test", "all"):
            continue
        ranking = group["ranking"]
        hit = ranking["hit_at_1"]
        exposed = group["exposed_hit1_identifiable"]
        coverage = group["answered_coverage"]
        lines.append(
            f"| {group['variant']} | {ranking['identifiable_faults']} | "
            f"{hit['numerator']}/{hit['denominator']} | "
            f"{exposed['numerator']}/{exposed['denominator']} | "
            f"{coverage['numerator']}/{coverage['denominator']} |"
        )
    lines += [
        "",
        "Candidate Hit@1 includes rankings that the system later declines to expose. "
        "Exposed Hit@1 includes abstentions as misses. Healthy and ambiguous cases are "
        "outside the unique-target denominator but remain in answer coverage. "
        "The frozen learned model is conservative; this is a small, already-opened "
        "synthetic test set, not production accuracy.",
        "",
        "## Source checks and claim review",
        "",
        f"The stratified AI review inspected {review['outputs']} outputs from "
        f"{review['episodes']} distinct episodes across all five systems and six categories. "
        f"Of {review['strict_support_denominator']} cited claims, "
        f"{review['claim_counts'].get('supported', 0)} were supported, "
        f"{review['claim_counts'].get('partial', 0)} partially supported and "
        f"{review['claim_counts'].get('unsupported', 0)} unsupported. "
        "Numeric and directed-call claims in the inspected sample matched their "
        "sources. Some hypotheses cited a service without evidence of abnormal behavior, "
        "or cited absolute latency without a baseline. Correct citations alone also "
        "failed to answer some temporal, path and outside-graph questions.",
        "",
        "The review does not establish inter-reviewer agreement. All prose was generated "
        "by the deterministic template; no Ollama generation quality is measured here.",
        "",
        "## Isolated temporal diagnostic",
        "",
        f"Across {temporal['episodes']} test episodes at an earlier cutoff, the filtered "
        f"arm exposed {temporal['filtered_future_context_items']} future context items. "
        f"The deliberately unrestricted arm used {temporal['unrestricted_future_context_items']} "
        f"future items among {temporal['unrestricted_context_items']} selected items, "
        f"affecting {temporal['episodes_with_future_context']} episodes. "
        f"Candidate rankings changed in {temporal['candidate_rankings_changed']} episodes; "
        f"exposed rankings changed in {temporal['exposed_rankings_changed']}.",
        "",
        "This joint ablation changes evidence eligibility and signal windows. It is an "
        "offline lookahead diagnostic, not a public runtime switch. Earlier-cutoff target "
        "accuracy was not scored because those target labels were not separately reviewed.",
        "",
        "## Published graph reconstruction",
        "",
        f"Read {graph['episodes']} explicit historical Neo4j test snapshots and checked "
        f"their PostgreSQL support records against source bytes. Edge precision was "
        f"{graph['micro_precision']['numerator']}/{graph['micro_precision']['denominator']}; "
        f"recall against the frozen generator topology was "
        f"{graph['micro_recall']['numerator']}/{graph['micro_recall']['denominator']}. "
        f"All {graph['resolved_observations']} supporting observations resolved, including "
        f"{graph['verified_parent_pairs']} measured parent chains. "
        "The reference includes known edges whose service endpoints are present. "
        "This measures synthetic topology reconstruction, not causal accuracy. "
        "Captured span-pair review is a separate check.",
        "",
        f"The explicit captured-pair AI review currently covers "
        f"{sum(r['reviewed_pairs'] for r in pairs['reviews'])} pairs from "
        f"{len(pairs['reviews'])} episodes/modes, spanning each selected episode's three "
        f"stages. Its status is `{pairs['status']}`; missing modes: "
        f"{', '.join(pairs['missing_modes']) or 'none'}. "
        "This purposive sample checks matching trace/parent identities, service direction "
        "and overlapping timestamps. A payment SERVER can finish after its CLIENT times "
        "out and still support an observed call. The sample is not an independent "
        "accuracy estimate or proof of the fault mechanism.",
        "",
        "## Reproducibility and limits",
        "",
        "- Two independent two-episode runs reproduced all 26 semantic outputs exactly.",
        "- Code, model, corpus, review, prompt and lock identities accompany predictions. "
        "The completed synthetic run's source bytes were archived before subsequent edits.",
        "- Timings exclude database preparation, distinguish embedding-cache hits/misses, "
        "and overlap real capture. They are local measurements, not service benchmarks.",
        "- Retrieval judgments are incomplete; recall/nDCG must be read with the "
        "reported judgment coverage. Ranking is not complete question-answer quality.",
        "- A twelve-case authored linking diagnostic matched all expected outcomes "
        "(five accepted links); it is a regression diagnostic, not held-out link accuracy.",
        "- Do not retune the frozen model using these test findings. Phase acceptance "
        "is recorded separately in the phase report.",
        "",
        "## Artifact identities",
        "",
    ]
    for path in (metrics_path, temporal_path, review_path, graph_path, pairs_path):
        lines.append(f"- `{path.relative_to(ROOT)}` — SHA-256 `{digest(path)}`.")
    if captured_complete:
        captured_metrics = captured_root / "metrics.json"
        lines += [""] + captured_section(
            json.loads(captured_metrics.read_bytes()),
            json.loads((captured_root / "completion.json").read_bytes()),
        )
        lines.append(f"Captured metrics SHA-256: `{digest(captured_metrics)}`.")
        claim_path = ROOT / "reports/phase13-captured-claim-review/validated_summary.json"
        if claim_path.exists():
            claims = json.loads(claim_path.read_bytes())
            lines += [
                "",
                f"Explicit AI review inspected {claims['outputs']} outputs from "
                f"{claims['episodes']} captured episodes (first schedule key per mode, "
                f"all five systems): {claims['claim_counts'].get('supported', 0)} supported, "
                f"{claims['claim_counts'].get('partial', 0)} partial, "
                f"{claims['claim_counts'].get('unsupported', 0)} unsupported and "
                f"{claims['claim_counts'].get('unavailable', 0)} unavailable claims. "
                "This purposive sample includes training/development cases and is not "
                "an independent estimate of held-out claim accuracy.",
                f"Claim summary SHA-256: `{digest(claim_path)}`.",
            ]
        else:
            lines += ["", "Captured semantic claim review: NOT RUN."]
    (ROOT / "docs/EVALUATION_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("Generated docs/EVALUATION_RESULTS.md from completed measured artifacts")


if __name__ == "__main__":
    main()
