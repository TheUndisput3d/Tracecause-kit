"""Frozen, resumable offline comparisons; labels are opened only for scoring."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tracecause.evaluation.metrics import (
    paired_episode_bootstrap,
    rate,
    score_ranking,
    score_retrieval,
    summarize_rankings,
)
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.investigation.templates import TEMPLATE_PROMPT_HASH
from tracecause.ranking.model import LinearRanker
from tracecause.retrieval.embeddings import EmbeddingCache, MiniLMEmbedder
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import database_url, make_engine, sessions

from .narratives import generate
from .prepare import ROOT, digest, write_json
from .ranker_test import MODEL_HASH
from .systems import CONFIG, SYSTEMS, load_episode, predict

CANONICAL_QUERY = "Which service should be checked first?"
VARIANTS = (
    ("graph_learned_without_documents", {"documents": False}),
    ("graph_learned_without_expansion", {"graph_expansion": False}),
    ("graph_learned_without_graph_features", {"graph_features": False}),
)


def validate_inputs(review: dict[str, Any], index: dict[str, Any]) -> None:
    if review["status"] not in {"ai_reviewed_source_resolved", "human_reviewed_source_resolved"}:
        raise ValueError("benchmark requires reviewed source-resolved questions")
    questions = review["questions"]
    if len({q["question_id"] for q in questions}) != len(questions):
        raise ValueError("duplicate question ID")
    if len({q["episode_id"] for q in questions}) != len(questions):
        raise ValueError("this protocol requires one reviewed question per episode")
    rows = index["episodes"]
    if len({row["episode_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate prepared episode")
    prepared = {row["episode_id"]: row["sha256"] for row in rows}
    groups: dict[str, str] = {}
    for question in questions:
        if prepared.get(question["episode_id"]) != question["prepared_sha256"]:
            raise ValueError("review/prepared episode mismatch")
        split = question["split"]
        if split not in {"train", "dev", "test"}:
            raise ValueError("unknown evaluation split")
        group = question["group_id"]
        if group in groups and groups[group] != split:
            raise ValueError("scenario group overlaps evaluation splits")
        groups[group] = split
        if question["identifiable"] and not question["accepted_target_services"]:
            raise ValueError("identifiable episode lacks accepted target")
        if len({j["evidence_id"] for j in question["evidence_judgments"]}) != len(
            question["evidence_judgments"]
        ):
            raise ValueError("duplicate evidence judgment")


def code_identity() -> str:
    paths = sorted([*ROOT.glob("src/**/*.py"), *ROOT.glob("scripts/evaluation/*.py")])
    return hashlib.sha256(
        json.dumps([(str(path.relative_to(ROOT)), digest(path)) for path in paths]).encode()
    ).hexdigest()


def semantic_prediction(value: dict[str, Any]) -> dict[str, Any]:
    """Fields that must reproduce exactly; measured timings/cache state may vary."""
    result = {key: item for key, item in value.items() if key not in {"cache", "timings_ms"}}
    if "generation" in result:
        result["generation"] = {
            key: item
            for key, item in result["generation"].items()
            if key != "generation_and_source_validation_ms"
        }
    return result


def latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def distribution(values: list[float]) -> dict[str, Any]:
        ordered = sorted(values)
        return {
            "runs": len(values),
            "p50_ms": statistics.median(values) if values else None,
            "p95_ms": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if values else None,
        }

    return {
        state: {
            stage: distribution(
                [row["timings_ms"][stage] for row in rows if (row["cache"]["misses"] > 0) == cold]
            )
            for stage in ("retrieval", "ranking", "total")
        }
        for state, cold in (("embedding_cache_miss", True), ("embedding_cache_hit", False))
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for category in ("all", row["category"]):
            buckets[(row["track"], row["split"], row["variant"], category)].append(row)
    summaries = []
    for (track, split, variant, category), selected in sorted(buckets.items()):
        rankings = [
            score_ranking(
                row["candidate_ranking"],
                row["accepted_target_keys"],
                identifiable=row["identifiable"],
            )
            for row in selected
        ]
        answered = [row for row in selected if row["exposed_ranking"]]
        eligible_answered = [row for row in answered if row["identifiable"]]
        ambiguous = [
            row for row in selected if not row["identifiable"] and row["accepted_target_keys"]
        ]
        healthy = [row for row in selected if not row["accepted_target_keys"]]

        def mean(field: str, selected: list[dict[str, Any]] = selected) -> dict[str, Any]:
            values = [
                row["retrieval_score"][field]
                for row in selected
                if row.get("retrieval_score") and row["retrieval_score"][field] is not None
            ]
            return {"value": statistics.mean(values) if values else None, "queries": len(values)}

        summaries.append(
            {
                "track": track,
                "split": split,
                "variant": variant,
                "category": category,
                "ranking": asdict(summarize_rankings(rankings)),
                "answered_coverage": asdict(rate(len(answered), len(selected))),
                "answered_identifiable_top1_error": asdict(
                    rate(
                        sum(
                            row["exposed_ranking"][0] not in row["accepted_target_keys"]
                            for row in eligible_answered
                        ),
                        len(eligible_answered),
                    )
                ),
                "healthy_hypothesis_rate": asdict(
                    rate(sum(bool(row["exposed_ranking"]) for row in healthy), len(healthy))
                ),
                "ambiguous_hypothesis_rate": asdict(
                    rate(sum(bool(row["exposed_ranking"]) for row in ambiguous), len(ambiguous))
                ),
                "exposed_hit1_identifiable": asdict(
                    rate(
                        sum(
                            bool(row["exposed_ranking"])
                            and row["exposed_ranking"][0] in row["accepted_target_keys"]
                            for row in selected
                            if row["identifiable"]
                        ),
                        sum(row["identifiable"] for row in selected),
                    )
                ),
                "retrieval_recall_at10": mean("recall"),
                "retrieval_ndcg_at10": mean("ndcg"),
                "judgment_coverage_at10": mean("judgment_coverage"),
                "citation_validity": asdict(
                    rate(
                        sum(row["generation"]["valid_unique_citations"] for row in selected),
                        sum(row["generation"]["unique_citations"] for row in selected),
                    )
                ),
                "numeric_consistency": asdict(
                    rate(
                        sum(row["generation"]["consistent_numeric_claims"] for row in selected),
                        sum(row["generation"]["numeric_claims"] for row in selected),
                    )
                ),
                "retrieval_median_ms": statistics.median(
                    row["timings_ms"]["retrieval"] for row in selected
                ),
                "latency_by_embedding_cache": latency_summary(selected),
                "prediction_median_ms": statistics.median(
                    row["timings_ms"]["total"] for row in selected
                ),
                "generation_validation_median_ms": statistics.median(
                    row["generation"]["generation_and_source_validation_ms"] for row in selected
                ),
            }
        )
    intervals = []
    for split in ("train", "dev", "test"):
        reference = {
            row["episode_id"]: row
            for row in rows
            if row["track"] == "canonical"
            and row["split"] == split
            and row["variant"] == "graph_learned"
            and row["identifiable"]
        }
        for variant in [*SYSTEMS[:-1], *(name for name, _ in VARIANTS)]:
            other = {
                row["episode_id"]: row
                for row in rows
                if row["track"] == "canonical"
                and row["split"] == split
                and row["variant"] == variant
                and row["identifiable"]
            }
            if reference and reference.keys() == other.keys():
                paired = {
                    key: (
                        float(row["ranking_score"]["hit_at_1"]),
                        float(other[key]["ranking_score"]["hit_at_1"]),
                    )
                    for key, row in reference.items()
                }
                intervals.append(
                    {
                        "split": split,
                        "left": "graph_learned",
                        "right": variant,
                        "metric": "candidate_hit1",
                        **asdict(paired_episode_bootstrap(paired)),
                    }
                )
    # Coverage comparisons remain defined for an entirely ambiguous/healthy split.
    # Higher coverage is not automatically better; target correctness is separate.
    for split in ("train", "dev", "test"):
        reference = {
            row["episode_id"]: row
            for row in rows
            if row["track"] == "canonical"
            and row["split"] == split
            and row["variant"] == "graph_learned"
        }
        for variant in [*SYSTEMS[:-1], *(name for name, _ in VARIANTS)]:
            other = {
                row["episode_id"]: row
                for row in rows
                if row["track"] == "canonical"
                and row["split"] == split
                and row["variant"] == variant
            }
            if reference and reference.keys() == other.keys():
                intervals.append(
                    {
                        "split": split,
                        "left": "graph_learned",
                        "right": variant,
                        "metric": "answered_coverage",
                        **asdict(
                            paired_episode_bootstrap(
                                {
                                    key: (
                                        float(bool(row["exposed_ranking"])),
                                        float(bool(other[key]["exposed_ranking"])),
                                    )
                                    for key, row in reference.items()
                                }
                            )
                        ),
                    }
                )
    return {"groups": summaries, "paired_episode_intervals": intervals}


def report(output: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    summary = summarize(rows)
    write_json(output / "metrics.json", {"manifest": manifest, **summary})
    columns = [
        "track",
        "split",
        "variant",
        "category",
        "episodes",
        "identifiable",
        "candidate_recall",
        "hit1",
        "hit3",
        "mrr",
        "answered_coverage",
        "recall_at10",
        "ndcg_at10",
        "judgment_coverage_at10",
    ]
    with (output / "metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for group in summary["groups"]:
            ranks = group["ranking"]
            writer.writerow(
                {
                    **{key: group[key] for key in columns[:4]},
                    "episodes": ranks["episodes"],
                    "identifiable": ranks["identifiable_faults"],
                    "candidate_recall": ranks["candidate_recall"]["value"],
                    "hit1": ranks["hit_at_1"]["value"],
                    "hit3": ranks["hit_at_3"]["value"],
                    "mrr": ranks["mrr"],
                    "answered_coverage": group["answered_coverage"]["value"],
                    "recall_at10": group["retrieval_recall_at10"]["value"],
                    "ndcg_at10": group["retrieval_ndcg_at10"]["value"],
                    "judgment_coverage_at10": group["judgment_coverage_at10"]["value"],
                }
            )
    failures = []
    for row in rows:
        if (
            row["identifiable"]
            and not row["ranking_score"]["hit_at_1"]
            or (not row["identifiable"] and row["exposed_ranking"])
        ):
            failures.append(
                {
                    key: row[key]
                    for key in (
                        "run_id",
                        "episode_id",
                        "split",
                        "track",
                        "variant",
                        "category",
                        "accepted_target_keys",
                        "candidate_ranking",
                        "exposed_ranking",
                        "identifiable",
                    )
                }
            )
    write_json(output / "error_analysis.json", failures)
    lines = [
        f"# {manifest.get('origin', 'synthetic')} template benchmark",
        "",
        "AI-reviewed evaluation labels; no independent human review. "
        "This run evaluates template generation, not Ollama.",
        "",
        "Candidate Hit@1 includes abstained rankings; exposed Hit@1 and answer "
        "coverage are separate in metrics.json. Unjudged retrieval items occupy ranks "
        "with zero gain; only judged relevant items enter the recall denominator.",
        "Undefined metrics have no eligible denominator. Coverage intervals compare "
        "answer rates, and do not establish which answers are correct.",
        "",
        "| Track | Test system | Episodes | Identifiable | Candidate Hit@1 | Answered |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for group in summary["groups"]:
        if group["split"] == "test" and group["category"] == "all":
            ranking = group["ranking"]
            hit, coverage = ranking["hit_at_1"], group["answered_coverage"]
            hit_text = (
                f"{hit['numerator']}/{hit['denominator']}"
                if hit["denominator"]
                else "undefined (n=0)"
            )
            lines.append(
                f"| {group['track']} | {group['variant']} | {ranking['episodes']} | "
                f"{ranking['identifiable_faults']} | {hit_text} | "
                f"{coverage['numerator']}/{coverage['denominator']} |"
            )
    lines += ["", "## Limits", "", *[f"- {text}" for text in manifest["limitations"]]]
    (output / "report.md").write_text("\n".join(lines) + "\n")


def run(prepared_root: Path, review_path: Path, output: Path, limit: int | None) -> dict[str, Any]:
    started = time.perf_counter()
    review = json.loads(review_path.read_bytes())
    index = json.loads((prepared_root / "index.json").read_bytes())
    if review["prepared_index_sha256"] != digest(prepared_root / "index.json"):
        raise ValueError("review/prepared index hash differs")
    validate_inputs(review, index)
    questions = sorted(review["questions"], key=lambda item: item["episode_id"])
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        questions = questions[:limit]
    identity = {
        "config": CONFIG,
        "code_sha256": code_identity(),
        "review_sha256": digest(review_path),
        "prepared_index_sha256": digest(prepared_root / "index.json"),
        "lock_sha256": digest(ROOT / "uv.lock"),
        "template_prompt_sha256": TEMPLATE_PROMPT_HASH,
        "limit": limit,
        "canonical_query": CANONICAL_QUERY,
        "variants": VARIANTS,
        "generation": "template",
        "concurrency": 1,
    }
    # JSON round-trip makes tuples stable across resume.
    identity = json.loads(json.dumps(identity))
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_bytes())
        if manifest["identity"] != identity:
            raise ValueError("frozen benchmark identity changed; use a new output root")
    else:
        manifest = {
            "identity": identity,
            "origin": "synthetic",
            "review_origin": review["review_origin"],
            "independent_human_review": review["independent_human_review"],
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "working_tree_dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
            "machine": {
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "cpu_description": next(
                    (
                        line.split(":", 1)[1].strip()
                        for line in Path("/proc/cpuinfo").read_text().splitlines()
                        if line.startswith("model name")
                    ),
                    "unknown",
                ),
                "thread_environment": {
                    key: os.environ.get(key)
                    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
                },
                "memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
            },
            "limitations": [
                "Small controlled synthetic dataset; no production generalization claim.",
                "Rule-assisted AI review by implementation author; no independent human "
                "validation.",
                "Earlier provisional ranker test opened these labels. No model/config tuning "
                "in this run; this is not a pristine first test access.",
                "Sparse final windows; deviation scores are not calibrated confidence.",
                "Evidence judgments are incomplete; report judgment coverage with retrieval "
                "metrics.",
                "Service ranking on reviewed questions is a diagnostic measure, not full "
                "task-answer correctness.",
                "Timing uses pre-exported database-verified evidence; excludes ingestion and "
                "graph construction. Capture ran concurrently.",
                "Source/numeric checks do not establish semantic claim support; separate "
                "claim review remains required.",
                "Temporal-filter ablation and captured track are pending; this is not the "
                "complete Phase 13 report.",
            ],
        }
        write_json(manifest_path, manifest)
    ranker = LinearRanker.from_path(
        ROOT / "models/candidate-logistic-v3/model.json", expected_hash=MODEL_HASH
    )
    cache = EmbeddingCache(ROOT / "reports/evaluation-embedding-cache", MiniLMEmbedder())
    artifacts = ArtifactRepository(ROOT / "reports/phase13-artifacts")
    engine = make_engine(
        database_url(
            host="127.0.0.1",
            port=15432,
            database="tracecause_evaluation_v1",
            user="postgres",
            password_file=ROOT / ".secrets/postgres_admin_password",
        )
    )
    rows = []
    try:
        with driver_from_auth_file(
            "bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth"
        ) as driver:
            reader = GraphReader(sessions(engine), driver)
            for index, question in enumerate(questions, 1):
                episode = load_episode(
                    prepared_root / f"{question['episode_id']}.json", question["prepared_sha256"]
                )
                if str(episode.snapshot_id) != question["snapshot_id"]:
                    raise ValueError("review snapshot mismatch")
                evidence_cache: dict[str, Any] = {}
                span_cache: dict[str, Any] = {}
                for track, text in (
                    ("canonical", CANONICAL_QUERY),
                    ("reviewed_question", question["question"]),
                ):
                    variants = [(system, system, {}) for system in SYSTEMS]
                    if track == "canonical":
                        variants.extend(
                            (name, "graph_learned", options) for name, options in VARIANTS
                        )
                    corpus_hashes = set()
                    for variant, system, options in variants:
                        run_id = f"{episode.episode_id}.{track}.{variant}"
                        path = output / "predictions" / f"{run_id}.json"
                        if path.exists():
                            saved = json.loads(path.read_bytes())
                            if saved["identity"] != identity or saved["run_id"] != run_id:
                                raise ValueError("prediction identity differs")
                            prediction = saved["prediction"]
                        else:
                            prediction = predict(episode, text, system, cache, ranker, **options)
                            prediction["generation"] = generate(
                                episode, prediction, reader, artifacts, evidence_cache, span_cache
                            )
                            write_json(
                                path,
                                {"identity": identity, "run_id": run_id, "prediction": prediction},
                            )
                        if not options:
                            corpus_hashes.add(prediction["corpus_hash"])
                        # Targets/judgments enter only after public-only prediction and generation.
                        targets = [
                            episode.names[name] for name in question["accepted_target_services"]
                        ]
                        ranking = score_ranking(
                            prediction["candidate_ranking"],
                            targets,
                            identifiable=question["identifiable"],
                        )
                        retrieval = None
                        if track == "reviewed_question":
                            retrieval = asdict(
                                score_retrieval(
                                    {
                                        item["evidence_id"]: item["grade"]
                                        for item in question["evidence_judgments"]
                                    },
                                    prediction["retrieved_ids"],
                                    k=10,
                                )
                            )
                        rows.append(
                            {
                                **prediction,
                                "run_id": run_id,
                                "track": track,
                                "variant": variant,
                                "split": question["split"],
                                "category": question["category"],
                                "group_id": question["group_id"],
                                "identifiable": question["identifiable"],
                                "accepted_target_keys": targets,
                                "ranking_score": asdict(ranking),
                                "retrieval_score": retrieval,
                            }
                        )
                    if len(corpus_hashes) != 1:
                        raise ValueError("baseline eligible corpora differ")
                write_json(
                    output / "progress.json",
                    {"completed_episodes": index, "episodes": len(questions), "runs": len(rows)},
                )
                print(
                    json.dumps({"episode": index, "total": len(questions), "runs": len(rows)}),
                    flush=True,
                )
    finally:
        engine.dispose()
    report(output, rows, manifest)
    write_json(output / "scored_predictions.json", rows)
    result = {
        "status": "synthetic_template_comparison_complete",
        "episodes": len(questions),
        "runs": len(rows),
        "partial_selection": limit is not None,
        "seconds": time.perf_counter() - started,
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "splits": dict(Counter(item["split"] for item in questions)),
    }
    write_json(output / "completion.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, default=ROOT / "reports/phase13-prepared-v2")
    parser.add_argument(
        "--review", type=Path, default=ROOT / "reports/phase13-questions/ai_resolved_questions.json"
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-benchmark-v1")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.prepared_root, args.review, args.output_root, args.limit)))


if __name__ == "__main__":
    main()
