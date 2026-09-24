"""Explicit container bootstrap tools; never invoked by HTTP request handlers."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tracecause.api.publish_report import publish
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import (
    EpisodeManifest,
    EvaluationReport,
    GenerationMode,
    InvestigationRequest,
    RankingMode,
    RetrievalMode,
    TemporalMode,
)
from tracecause.graph.schema import migrate_graph
from tracecause.storage.schema import EpisodeRow

HEARTBEAT = Path("/tmp/tracecause-worker-heartbeat")


def prefetch() -> None:
    from huggingface_hub import snapshot_download

    from tracecause.investigation.generation import PINNED_TOKENIZER_REVISION
    from tracecause.retrieval.embeddings import MODEL_REVISION, MiniLMEmbedder

    patterns = ["*.json", "*.txt", "*.safetensors", "1_Pooling/*"]
    snapshot_download(
        "sentence-transformers/all-MiniLM-L6-v2", revision=MODEL_REVISION, allow_patterns=patterns
    )
    snapshot_download(
        "Qwen/Qwen3-4B-Instruct-2507",
        revision=PINNED_TOKENIZER_REVISION,
        allow_patterns=[
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
        ],
    )
    MiniLMEmbedder()
    print(json.dumps({"pinned_embeddings_and_tokenizer_ready": True, "generation_weights": False}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "prefetch", "seed", "worker-health"))
    args = parser.parse_args()
    if args.action == "prefetch":
        prefetch()
        return
    settings = load_settings(Path("configs/defaults.yaml"))
    resources = open_resources(settings)
    try:
        if args.action == "migrate":
            if settings.pg_user != "postgres":
                raise ValueError("migration requires the explicit administrator role")
            config = Config("alembic.ini")
            assert resources.engine is not None
            with resources.engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                connection.execute(text("GRANT USAGE ON SCHEMA public TO tracecause_app"))
                connection.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                        "IN SCHEMA public TO tracecause_app"
                    )
                )
                connection.execute(
                    text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tracecause_app")
                )
            print(json.dumps(migrate_graph(resources.driver)))
        elif args.action == "worker-health":
            age = time.time() - HEARTBEAT.stat().st_mtime
            if age < 0 or age > 600:
                raise RuntimeError("worker heartbeat is stale")
            with resources.factory() as session:
                session.execute(text("SELECT 1"))
            resources.driver.verify_connectivity()
        else:
            if settings.public_read_only:
                raise ValueError("seed requires explicit operator mode")
            manifest = EpisodeManifest.model_validate_json(
                Path("data/demo/episode/manifest.json").read_bytes()
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tracecause",
                    "investigation",
                    "seed",
                    "--episode-dir",
                    "data/demo/episode",
                    "--corpus-dir",
                    "data/runbooks/demo",
                    "--artifact-root",
                    str(settings.artifact_root),
                    "--db-host",
                    settings.pg_host,
                    "--db-port",
                    str(settings.pg_port),
                    "--db-name",
                    settings.pg_database,
                    "--db-user",
                    settings.pg_user,
                    "--password-file",
                    os.environ["TRACECAUSE_PG_PASSWORD_FILE"],
                    "--neo4j-uri",
                    settings.neo4j_uri,
                    "--neo4j-auth-file",
                    os.environ["TRACECAUSE_NEO4J_AUTH_FILE"],
                ],
                check=True,
                timeout=180,
            )
            with resources.factory() as session:
                episode = session.get(EpisodeRow, manifest.episode_id)
                assert episode is not None and episode.active_snapshot_id is not None
                snapshot = episode.active_snapshot_id
            result = resources.investigations.investigate(
                manifest.workspace_id,
                InvestigationRequest(
                    episode_id=manifest.episode_id,
                    snapshot_id=snapshot,
                    as_of=manifest.event_interval.end,
                    temporal_mode=TemporalMode.EVENT,
                    question="Review the eligible measurements and source evidence.",
                    retrieval_mode=RetrievalMode.GRAPH,
                    ranking_mode=RankingMode.HEURISTIC,
                    generation_mode=GenerationMode.TEMPLATE,
                ),
            )
            for evidence in result.evidence:
                resources.investigations.evidence(
                    manifest.workspace_id, result.investigation_id, evidence.evidence_id
                )
            for report_id in (
                "8574b985-f4d2-5e45-b901-93b66bf30fa0",
                "5e9a68df-fa4c-510a-91fd-d8f1db682235",
            ):
                publish(
                    resources,
                    EvaluationReport.model_validate_json(
                        Path(f"data/evaluation/published/{report_id}.json").read_bytes()
                    ),
                )
            print(
                json.dumps(
                    {
                        "episode_id": str(manifest.episode_id),
                        "snapshot_id": str(snapshot),
                        "investigation_id": str(result.investigation_id),
                        "citations_resolved": len(result.evidence),
                        "generation_origin": result.generation_origin,
                    }
                )
            )
    finally:
        resources.close()


if __name__ == "__main__":
    main()
