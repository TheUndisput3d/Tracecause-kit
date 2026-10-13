"""Isolated early-cutoff versus future-window diagnostic; never a runtime mode.

The unrestricted arm deliberately uses the episode-end export to demonstrate lookahead.
It changes signal windows as well as evidence eligibility and is labeled accordingly.
No target accuracy is assigned: episode-end labels are not judgments at the earlier cutoff.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from tracecause.graph.projection import build_snapshot
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.ranking.model import LinearRanker
from tracecause.retrieval.embeddings import EmbeddingCache, MiniLMEmbedder
from tracecause.retrieval.engine import EvidenceCandidate, RetrievalRequest, corpus_from_reader
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import database_url, make_engine, sessions
from tracecause.telemetry.persistence import compute_persisted_signals, materialize_signals

from ..capture.validation import timestamp_ns
from .benchmark import CANONICAL_QUERY, code_identity
from .prepare import ROOT, digest, write_json
from .ranker_test import MODEL_HASH
from .systems import PublicEpisode, load_episode, predict


def future_ids(
    corpus: tuple[EvidenceCandidate, ...], identifiers: list[str], cutoff: int
) -> list[str]:
    by_id = {item.evidence_id: item for item in corpus}
    result = []
    for key in identifiers:
        item = by_id[key]
        if item.kind == "observation":
            future = item.source_locator["end_ns"] > cutoff
        else:
            published = item.source_locator.get("published_at")
            future = not published or timestamp_ns(published) > cutoff
        if future:
            result.append(key)
    return result


def main() -> None:
    root = ROOT / "reports/phase13-temporal-ablation-v1"
    prepared = ROOT / "reports/phase13-prepared-v2"
    reviewed = ROOT / "reports/phase13-questions/ai_resolved_questions.json"
    questions = json.loads(reviewed.read_bytes())["questions"]
    selected = sorted(
        (item for item in questions if item["split"] == "test"), key=lambda item: item["episode_id"]
    )
    manifest = {
        "version": "early-versus-unrestricted-window-v1",
        "scope": "offline_evaluation_only_no_public_runtime_switch",
        "system": "graph_learned",
        "query": CANONICAL_QUERY,
        "query_cutoff": "investigation_start_plus_120_seconds",
        "unrestricted_arm": "episode_end_evidence_and_signal_windows",
        "prepared_index_sha256": digest(prepared / "index.json"),
        "review_sha256": digest(reviewed),
        "model_sha256": MODEL_HASH,
        "code_sha256": code_identity(),
        "episodes": len(selected),
        "target_accuracy": "not_scored_without_earlier_cutoff_target_review",
        "narrative_generation": "not_run_for_this_ablation",
        "limitation": (
            "This joint temporal ablation changes signal windows and evidence "
            "eligibility; it does not isolate their individual effects."
        ),
    }
    if (root / "manifest.json").exists():
        if json.loads((root / "manifest.json").read_bytes()) != manifest:
            raise ValueError("temporal experiment identity changed")
    else:
        write_json(root / "manifest.json", manifest)
    engine = make_engine(
        database_url(
            host="127.0.0.1",
            port=15432,
            database="tracecause_evaluation_v1",
            user="postgres",
            password_file=ROOT / ".secrets/postgres_admin_password",
        )
    )
    factory = sessions(engine)
    artifacts = ArtifactRepository(ROOT / "reports/phase13-artifacts")
    cache = EmbeddingCache(ROOT / "reports/evaluation-embedding-cache", MiniLMEmbedder())
    ranker = LinearRanker.from_path(
        ROOT / "models/candidate-logistic-v3/model.json", expected_hash=MODEL_HASH
    )
    rows = []
    try:
        with driver_from_auth_file(
            "bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth"
        ) as driver:
            reader = GraphReader(factory, driver)
            for number, item in enumerate(selected, 1):
                output = root / f"{item['episode_id']}.json"
                if output.exists():
                    row = json.loads(output.read_bytes())
                    if row["manifest"] != manifest:
                        raise ValueError("temporal result identity differs")
                    rows.append(row)
                    continue
                late = load_episode(
                    prepared / f"{item['episode_id']}.json", item["prepared_sha256"]
                )
                cutoff = late.report.investigation_start_ns + 120_000_000_000
                if cutoff >= late.cutoff:
                    raise ValueError("temporal diagnostic requires a later source window")
                materialize_signals(
                    factory,
                    artifacts,
                    workspace_id=late.workspace_id,
                    episode_id=late.episode_id,
                    investigation_start_ns=late.report.investigation_start_ns,
                    as_of_ns=cutoff,
                )
                snapshot = build_snapshot(
                    factory,
                    driver,
                    workspace_id=late.workspace_id,
                    episode_id=late.episode_id,
                    artifacts=artifacts,
                )
                report = compute_persisted_signals(
                    factory,
                    workspace_id=late.workspace_id,
                    episode_id=late.episode_id,
                    as_of_ns=cutoff,
                )
                request = RetrievalRequest(
                    late.workspace_id,
                    late.episode_id,
                    snapshot.snapshot_id,
                    cutoff,
                    "event",
                    CANONICAL_QUERY,
                )
                corpus = corpus_from_reader(reader, artifacts, request)
                calls = reader.eligible_calls(
                    workspace_id=late.workspace_id,
                    episode_id=late.episode_id,
                    snapshot_id=snapshot.snapshot_id,
                    as_of_ns=cutoff,
                )
                early = PublicEpisode(
                    late.workspace_id,
                    late.episode_id,
                    snapshot.snapshot_id,
                    cutoff,
                    report,
                    corpus,
                    calls,
                    late.names,
                )
                valid = predict(early, CANONICAL_QUERY, "graph_learned", cache, ranker)
                unfiltered = predict(late, CANONICAL_QUERY, "graph_learned", cache, ranker)
                valid_future = future_ids(early.corpus, valid["context_ids"], cutoff)
                if valid_future:
                    raise ValueError("filtered temporal arm admitted future evidence")
                row = {
                    "manifest": manifest,
                    "episode_id": item["episode_id"],
                    "query_cutoff_ns": cutoff,
                    "filtered": valid,
                    "unrestricted": unfiltered,
                    "filtered_future_context_ids": valid_future,
                    "unrestricted_future_context_ids": future_ids(
                        late.corpus, unfiltered["context_ids"], cutoff
                    ),
                    "candidate_ranking_changed": valid["candidate_ranking"]
                    != unfiltered["candidate_ranking"],
                    "exposed_ranking_changed": valid["exposed_ranking"]
                    != unfiltered["exposed_ranking"],
                    "early_snapshot_id": str(snapshot.snapshot_id),
                    "early_signal_report": asdict(report),
                }
                write_json(output, row)
                rows.append(row)
                print(json.dumps({"episodes": number, "total": len(selected)}), flush=True)
    finally:
        engine.dispose()
    summary: dict[str, Any] = {
        "status": "completed_isolated_temporal_diagnostic",
        "episodes": len(rows),
        "filtered_future_context_items": sum(
            len(row["filtered_future_context_ids"]) for row in rows
        ),
        "unrestricted_future_context_items": sum(
            len(row["unrestricted_future_context_ids"]) for row in rows
        ),
        "unrestricted_context_items": sum(len(row["unrestricted"]["context_ids"]) for row in rows),
        "episodes_with_future_context": sum(
            bool(row["unrestricted_future_context_ids"]) for row in rows
        ),
        "candidate_rankings_changed": sum(row["candidate_ranking_changed"] for row in rows),
        "exposed_rankings_changed": sum(row["exposed_ranking_changed"] for row in rows),
    }
    write_json(root / "summary.json", {"manifest": manifest, **summary})
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
