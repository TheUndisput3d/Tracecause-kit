"""Fair offline systems over the same exported, database-verified public evidence.

No labels are accepted by this module. Retrieval uses the production scoring functions;
non-graph candidates come exclusively from literal service mentions in retrieved text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from tracecause.documents.markdown import model_tokenizer
from tracecause.graph.queries import EligibleCall
from tracecause.investigation.ranking import generate_candidates, is_anomalous, score_candidates
from tracecause.ranking.features import candidate_features
from tracecause.ranking.model import LinearRanker
from tracecause.retrieval.embeddings import EmbeddingCache, MiniLMEmbedder
from tracecause.retrieval.engine import (
    EvidenceCandidate,
    dense_rank,
    expand_graph,
    lexical_rank,
    rrf_fuse,
)
from tracecause.telemetry.signals import ComputedObservation, SignalFeature, SignalReport

from ..capture.validation import timestamp_ns
from .prepare import ROOT, digest, write_json
from .ranker_test import MODEL_HASH

System = Literal["anomaly", "dense", "hybrid", "graph_heuristic", "graph_learned"]
SYSTEMS: tuple[System, ...] = ("anomaly", "dense", "hybrid", "graph_heuristic", "graph_learned")
CONFIG = {
    "version": "five-systems-v1",
    "generation": "template",
    "top_k": 10,
    "retrieval_limit": 40,
    "context_items": 12,
    "context_tokens": 1800,
    "max_candidates": 20,
    "max_hypotheses": 3,
    "model_hash": MODEL_HASH,
    "non_graph_candidates": "literal service mentions in retrieved text",
    "non_graph_ranking": "sum reciprocal evidence ranks (k=60), no graph features",
    "anomaly_ranking": "maximum positive error/latency/absolute-volume deviation",
    "ranking_denominator": "all identifiable episodes including candidate misses",
    "review_origin": "ai",
    "independent_human_review": False,
}


@dataclass(frozen=True)
class PublicEpisode:
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    cutoff: int
    report: SignalReport
    corpus: tuple[EvidenceCandidate, ...]
    calls: tuple[EligibleCall, ...]
    names: dict[str, str]


def load_episode(path: Path, expected_hash: str) -> PublicEpisode:
    if digest(path) != expected_hash:
        raise ValueError("prepared episode hash mismatch")
    raw = json.loads(path.read_bytes())
    workspace_id, episode_id, snapshot_id = (
        UUID(raw[key]) for key in ("workspace_id", "episode_id", "snapshot_id")
    )
    cutoff = raw["as_of_ns"]
    report = dict(raw["signal_report"])
    report.update(workspace_id=UUID(report["workspace_id"]), episode_id=UUID(report["episode_id"]))
    signals = []
    for source in report["signals"]:
        item = dict(source)
        item["analysis_observation_id"] = UUID(item["analysis_observation_id"])
        for name in ("baseline_observation_ids", "analysis_bucket_ids"):
            item[name] = tuple(UUID(value) for value in item[name])
        item["flags"] = tuple(item["flags"])
        signals.append(SignalFeature(**item))
    observations = []
    for source in report["observations"]:
        item = dict(source)
        for name in ("id", "workspace_id", "episode_id"):
            item[name] = UUID(item[name])
        item["supports"] = tuple(tuple(value) for value in item["supports"])
        item["available_at"] = (
            datetime.fromisoformat(item["available_at"]) if item["available_at"] else None
        )
        observations.append(ComputedObservation(**item))
    report.update(
        signals=tuple(signals),
        observations=tuple(observations),
        quality_flags=tuple(report["quality_flags"]),
    )
    signal_report = SignalReport(**report)
    if (
        signal_report.workspace_id != workspace_id
        or signal_report.episode_id != episode_id
        or signal_report.as_of_ns != cutoff
    ):
        raise ValueError("prepared signal scope mismatch")
    corpus = []
    for source in raw["corpus"]:
        item = dict(source)
        for key in ("workspace_id", "episode_id", "snapshot_id"):
            item[key] = UUID(item[key])
        item["service_keys"] = tuple(item["service_keys"])
        candidate = EvidenceCandidate(**item)
        if (
            candidate.workspace_id,
            candidate.episode_id,
            candidate.snapshot_id,
            candidate.as_of_ns,
        ) != (workspace_id, episode_id, snapshot_id, cutoff):
            raise ValueError("prepared evidence scope mismatch")
        if candidate.kind == "observation" and candidate.source_locator["end_ns"] > cutoff:
            raise ValueError("future observation in eligible corpus")
        if candidate.kind == "chunk" and (
            not candidate.source_locator.get("published_at")
            or timestamp_ns(candidate.source_locator["published_at"]) > cutoff
        ):
            raise ValueError("unpublished or future document in eligible corpus")
        corpus.append(candidate)
    if len({item.evidence_id for item in corpus}) != len(corpus):
        raise ValueError("duplicate prepared evidence ID")
    calls = tuple(
        EligibleCall(
            item["caller_service_key"],
            item["callee_service_key"],
            tuple(UUID(value) for value in item["support_observation_ids"]),
        )
        for item in raw["calls"]
    )
    return PublicEpisode(
        workspace_id,
        episode_id,
        snapshot_id,
        cutoff,
        signal_report,
        tuple(corpus),
        calls,
        raw["service_keys"],
    )


def mentioned_services(text: str, names: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                key
                for name, key in names.items()
                if name
                and key
                and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
            }
        )
    )


def predict(
    episode: PublicEpisode,
    question: str,
    system: System,
    cache: EmbeddingCache,
    ranker: LinearRanker,
    *,
    documents: bool = True,
    graph_expansion: bool = True,
    graph_features: bool = True,
) -> dict[str, Any]:
    if system not in SYSTEMS:
        raise ValueError("unknown benchmark system")
    started = time.perf_counter()
    corpus = tuple(item for item in episode.corpus if documents or item.kind != "chunk")
    by_id = {item.evidence_id: item for item in corpus}
    report = episode.report
    signals = {item.service_key: item for item in report.signals}
    symptom = episode.names.get("storefront")
    lexical = dense = ()
    cache_state = {"hits": 0, "misses": 0}
    if system != "anomaly":
        dense, cache_state = dense_rank(question, corpus, cache)
    if system in {"hybrid", "graph_heuristic", "graph_learned"}:
        lexical = lexical_rank(question, corpus)
    fused = rrf_fuse({"dense": dense, "lexical": lexical})
    retrieval_scores = {key: score for key, score, _ in fused}
    truncated = False
    if system.startswith("graph") and graph_expansion:
        seeds = tuple(
            dict.fromkeys(
                [
                    *([symptom] if symptom else []),
                    *(
                        key
                        for evidence_id, _, _ in fused[:40]
                        for key in by_id[evidence_id].service_keys
                    ),
                ]
            )
        )[:20]
        additions, _, truncated = expand_graph(episode.calls, seeds, by_id)
        for key, score in additions.items():
            retrieval_scores[key] = retrieval_scores.get(key, 0.0) + score / 61.0
    retrieval_ids = sorted(retrieval_scores, key=lambda key: (-retrieval_scores[key], key))[:40]
    retrieval_ms = (time.perf_counter() - started) * 1000
    candidate_scores: dict[str, float] = {}
    if system == "anomaly":
        candidate_scores = {
            item.service_key: max(
                item.latency_deviation_z or 0.0,
                item.error_excess_z or 0.0,
                abs(item.volume_deviation_z or 0.0),
            )
            for item in report.signals
            if is_anomalous(item)
        }
        retrieval_ids = [
            f"observation:{signals[key].analysis_observation_id}"
            for key in sorted(candidate_scores, key=lambda key: (-candidate_scores[key], key))
        ]
        retrieval_ids = [key for key in retrieval_ids if key in by_id]
    elif system in {"dense", "hybrid"}:
        for index, evidence_id in enumerate(retrieval_ids):
            for key in mentioned_services(by_id[evidence_id].text, episode.names):
                if key in signals:
                    candidate_scores[key] = candidate_scores.get(key, 0.0) + 1 / (61 + index)
    else:
        keys, candidate_truncated = generate_candidates(report.signals, episode.calls, symptom)
        truncated |= candidate_truncated
        calls = episode.calls if graph_features else ()
        if system == "graph_learned":
            for feature in candidate_features(report, calls, keys, symptom, question):
                candidate_scores[feature.service_key] = ranker.score(feature)[0]
        else:
            candidate_scores = {
                item.service_key: item.score
                for item in score_candidates(report.signals, calls, keys, symptom)
            }
    ranking = sorted(candidate_scores, key=lambda key: (-candidate_scores[key], key))[:20]
    adequate = any(
        "insufficient_baseline" not in signal.flags and "no_analysis_samples" not in signal.flags
        for signal in report.signals
    )
    status = (
        "insufficient_evidence"
        if not adequate
        else "supported_hypotheses"
        if any(is_anomalous(signal) for signal in report.signals)
        else "no_anomaly_detected"
    )
    threshold = ranker.artifact["validation"]["rejection"]["threshold"]
    if (
        system == "graph_learned"
        and status == "supported_hypotheses"
        and (not ranking or threshold is not None and candidate_scores[ranking[0]] < threshold)
    ):
        status = "insufficient_evidence"
    ranking_ms = (time.perf_counter() - started) * 1000 - retrieval_ms
    # Identical support-first context policy and limits for every system.
    support = (
        [f"observation:{signals[key].analysis_observation_id}" for key in ranking[:3]]
        if status == "supported_hypotheses"
        else []
    )
    selected: list[str] = []
    tokens = 0
    tokenizer = model_tokenizer()
    for key in dict.fromkeys([*support, *retrieval_ids]):
        if key not in by_id:
            continue
        count = len(tokenizer.encode(by_id[key].text, add_special_tokens=False).ids)
        if len(selected) >= CONFIG["context_items"]:
            break
        if tokens + count <= CONFIG["context_tokens"]:
            selected.append(key)
            tokens += count
    exposed = ranking[:3] if status == "supported_hypotheses" else []
    if (
        not exposed
        and status == "supported_hypotheses"
        or any(key not in selected for key in support)
    ):
        status, exposed = "insufficient_evidence", []
    return {
        "system": system,
        "episode_id": str(episode.episode_id),
        "snapshot_id": str(episode.snapshot_id),
        "as_of_ns": episode.cutoff,
        "question": question,
        "status": status,
        "candidate_ranking": ranking,
        "candidate_scores": candidate_scores,
        "exposed_ranking": exposed,
        "retrieved_ids": retrieval_ids,
        "context_ids": selected,
        "context_tokens": tokens,
        "graph_truncated": truncated,
        "cache": cache_state,
        "corpus_hash": hashlib.sha256(
            json.dumps([item.evidence_id for item in corpus]).encode()
        ).hexdigest(),
        "ablations": {
            "documents": documents,
            "graph_expansion": graph_expansion,
            "graph_features": graph_features,
        },
        "timings_ms": {
            "retrieval": retrieval_ms,
            "ranking": ranking_ms,
            "total": (time.perf_counter() - started) * 1000,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, default=ROOT / "reports/phase13-prepared-v2")
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-system-smoke")
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    index = json.loads((args.prepared_root / "index.json").read_bytes())
    cache = EmbeddingCache(ROOT / "reports/evaluation-embedding-cache", MiniLMEmbedder())
    ranker = LinearRanker.from_path(
        ROOT / "models/candidate-logistic-v3/model.json", expected_hash=MODEL_HASH
    )
    output = []
    for row in index["episodes"][: args.limit]:
        episode = load_episode(args.prepared_root / f"{row['episode_id']}.json", row["sha256"])
        runs = [
            predict(episode, "Which service should be checked first?", system, cache, ranker)
            for system in SYSTEMS
        ]
        if len({run["corpus_hash"] for run in runs}) != 1:
            raise ValueError("baseline systems received different eligible corpora")
        output.extend(runs)
        print(
            json.dumps(
                {
                    "episode": row["episode_id"],
                    "systems": len(runs),
                    "candidate_counts": [len(run["candidate_ranking"]) for run in runs],
                }
            ),
            flush=True,
        )
    write_json(
        args.output_root / "predictions.json",
        {
            "status": "unscored_system_smoke",
            "config": CONFIG,
            "runs": output,
            "gold_access": False,
            "narrative_generation": "not_run",
        },
    )


if __name__ == "__main__":
    main()
