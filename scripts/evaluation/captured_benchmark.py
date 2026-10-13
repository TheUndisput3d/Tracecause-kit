"""Separate frozen synthetic-model transfer report over reviewed actual captures."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracecause.evaluation.metrics import score_ranking
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.investigation.templates import TEMPLATE_PROMPT_HASH
from tracecause.ranking.model import LinearRanker
from tracecause.retrieval.embeddings import EmbeddingCache, MiniLMEmbedder
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import database_url, make_engine, sessions

from ..capture.review import validate_review
from .benchmark import CANONICAL_QUERY, code_identity, report
from .narratives import generate
from .prepare import ROOT, digest, write_json
from .ranker_test import MODEL_HASH
from .systems import CONFIG, SYSTEMS, load_episode, predict


def validate_prediction(
    prediction: dict[str, Any], *, episode_id: str, snapshot_id: str, cutoff: int, system: str
) -> None:
    expected = {
        "episode_id": episode_id,
        "snapshot_id": snapshot_id,
        "as_of_ns": cutoff,
        "system": system,
        "question": CANONICAL_QUERY,
    }
    if any(prediction.get(key) != value for key, value in expected.items()):
        raise ValueError("captured prediction has different scope, query or system")


def select_reviewed(
    review: dict[str, Any], prepared: dict[str, Any], *, smoke: bool
) -> list[dict[str, Any]]:
    rows = prepared["episodes"]
    hashes = {row["episode_id"]: row["sha256"] for row in rows}
    if len(hashes) != len(rows):
        raise ValueError("duplicate prepared capture")
    labels = review["reviewed_labels"]
    if len({item["episode_id"] for item in labels}) != len(labels):
        raise ValueError("duplicate reviewed capture")
    if not smoke and (
        review["status"] != "representative_ai_reviewed"
        or not review["all_test_runs_reviewed"]
        or review["dataset_runs"] != 24
        or len(labels) != 24
        or len(hashes) != 24
    ):
        raise ValueError("full captured benchmark requires all 24 prepared and reviewed captures")
    selected = sorted(
        (item for item in labels if not smoke or item["split"] == "train"),
        key=lambda item: item["episode_id"],
    )
    if smoke:
        selected = selected[:2]
    if not selected or any(item["episode_id"] not in hashes for item in selected):
        raise ValueError("reviewed captured input is missing")
    return [{**item, "prepared_sha256": hashes[item["episode_id"]]} for item in selected]


def run(smoke: bool, output_root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    capture = ROOT / "reports/capture"
    review_path = capture / "ai_review_validation.json"
    validate_review(
        capture / "audit.json", capture / "ai_review_sheet.csv", review_path, reviewer_kind="ai"
    )
    review = json.loads(review_path.read_bytes())
    prepared_root = ROOT / "reports/phase13-captured-prepared-v1"
    prepared = json.loads((prepared_root / "index.json").read_bytes())
    selected = select_reviewed(review, prepared, smoke=smoke)
    output = output_root or ROOT / (
        "reports/phase13-captured-smoke-v1" if smoke else "reports/phase13-captured-benchmark-v1"
    )
    identity = {
        "origin": "captured_demo",
        "review_origin": "ai",
        "independent_human_review": False,
        "config": CONFIG,
        "code_sha256": code_identity(),
        "model_sha256": MODEL_HASH,
        "query": CANONICAL_QUERY,
        "cutoff_after_closed_baseline_seconds": 240,
        "prepared_index_sha256": digest(prepared_root / "index.json"),
        "review_sha256": digest(review_path),
        "smoke": smoke,
        "lock_sha256": digest(ROOT / "uv.lock"),
        "template_prompt_sha256": TEMPLATE_PROMPT_HASH,
        "concurrency": 1,
        "generation": "template",
    }
    metadata = {
        "limitations": [
            "Small controlled MiniShop transfer sample, not production validation.",
            "Frozen synthetic-trained model; no captured fitting or threshold tuning.",
            "Explicit AI review by implementation author; no independent human validation.",
            "Template generation only. Captured evidence retrieval relevance is not scored.",
            "Canonical ranking/citation check only; semantic claim support needs review.",
            "Offline exported evidence; timings exclude preparation and may overlap capture.",
        ],
    }
    if (output / "manifest.json").exists():
        manifest = json.loads((output / "manifest.json").read_bytes())
        if manifest.get("identity") != identity:
            raise ValueError("captured benchmark identity changed; preserve previous output")
    else:
        manifest = {
            **metadata,
            "identity": identity,
            "origin": "captured_demo",
            "started_at": datetime.now(UTC).isoformat(),
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
                "memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
                "thread_environment": {
                    key: os.environ.get(key)
                    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
                },
            },
        }
        write_json(output / "manifest.json", manifest)
    cache = EmbeddingCache(ROOT / "reports/evaluation-embedding-cache", MiniLMEmbedder())
    ranker = LinearRanker.from_path(
        ROOT / "models/candidate-logistic-v3/model.json", expected_hash=MODEL_HASH
    )
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
    resumed, computed = 0, 0
    try:
        with driver_from_auth_file(
            "bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth"
        ) as driver:
            reader = GraphReader(sessions(engine), driver)
            for item in selected:
                episode = load_episode(
                    prepared_root / f"{item['episode_id']}.json", item["prepared_sha256"]
                )
                evidence_cache: dict[str, Any] = {}
                span_cache: dict[str, Any] = {}
                corpus_hashes = set()
                for system in SYSTEMS:
                    run_id = f"{episode.episode_id}.canonical.{system}"
                    path = output / "predictions" / f"{run_id}.json"
                    if path.exists():
                        saved = json.loads(path.read_bytes())
                        if saved["manifest"] != manifest:
                            raise ValueError("captured prediction identity changed")
                        prediction = saved["prediction"]
                        resumed += 1
                    else:
                        prediction = predict(episode, CANONICAL_QUERY, system, cache, ranker)
                        prediction["generation"] = generate(
                            episode, prediction, reader, artifacts, evidence_cache, span_cache
                        )
                        write_json(path, {"manifest": manifest, "prediction": prediction})
                        computed += 1
                    validate_prediction(
                        prediction,
                        episode_id=str(episode.episode_id),
                        snapshot_id=str(episode.snapshot_id),
                        cutoff=episode.cutoff,
                        system=system,
                    )
                    corpus_hashes.add(prediction["corpus_hash"])
                    targets = [episode.names[name] for name in item["accepted_target_services"]]
                    ranking = score_ranking(
                        prediction["candidate_ranking"], targets, identifiable=item["identifiable"]
                    )
                    rows.append(
                        {
                            **prediction,
                            "run_id": run_id,
                            "track": "canonical",
                            "variant": system,
                            "split": item["split"],
                            "category": item["mode"],
                            "group_id": item["key"],
                            "identifiable": item["identifiable"],
                            "accepted_target_keys": targets,
                            "ranking_score": asdict(ranking),
                            "retrieval_score": None,
                        }
                    )
                if len(corpus_hashes) != 1:
                    raise ValueError("captured systems did not receive the same eligible corpus")
                print(json.dumps({"episode_id": item["episode_id"], "runs": len(rows)}), flush=True)
    finally:
        engine.dispose()
    report(output, rows, manifest)
    write_json(output / "scored_predictions.json", rows)
    summary = {
        "status": "captured_training_smoke_complete" if smoke else "captured_transfer_complete",
        "episodes": len(selected),
        "runs": len(rows),
        "smoke": smoke,
        "resumed_predictions": resumed,
        "new_predictions": computed,
        "invocation_elapsed_seconds": time.perf_counter() - started,
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "provider_requests": 0,
        "provider_tokens": 0,
        "output_token_measure": "MiniLM token counts in each prediction; not LLM usage",
    }
    write_json(output / "completion.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.smoke, args.output_root)))


if __name__ == "__main__":
    main()
