"""Operator seed, investigate, inspect, export and citation smoke commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from neo4j import Driver
from neo4j.exceptions import Neo4jError
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tracecause.cli_storage import _connection_args
from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.models import (
    EpisodeManifest,
    GenerationMode,
    InvestigationRequest,
    RankingMode,
    RetrievalMode,
    TemporalMode,
)
from tracecause.documents.persistence import ingest_corpus
from tracecause.graph.projection import build_snapshot
from tracecause.graph.schema import driver_from_auth_file
from tracecause.investigation.generation import (
    PINNED_MANIFEST_DIGEST,
    OllamaProvider,
)
from tracecause.investigation.service import InvestigationService
from tracecause.investigation.templates import ns_datetime
from tracecause.jobs.imports import ImportWorker, submit_import
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import (
    DEFAULT_WORKSPACE_ID,
    closed_analysis_boundary,
    database_url,
    make_engine,
    sessions,
)
from tracecause.storage.schema import EpisodeRow, JobRow
from tracecause.telemetry.persistence import materialize_signals


def _ns(value: object) -> int:
    from datetime import datetime

    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def add_investigation_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    investigation = subparsers.add_parser("investigation", help="seed and run cited investigations")
    actions = investigation.add_subparsers(dest="investigation_action", required=True)
    for name in ("seed", "run", "show", "evidence", "export", "smoke"):
        command = actions.add_parser(name)
        _connection_args(command)
        command.add_argument("--artifact-root", type=Path, required=True)
        command.add_argument("--embedding-cache-root", type=Path)
        command.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)
        if name in {"seed", "run", "evidence", "smoke"}:
            command.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
            command.add_argument("--neo4j-auth-file", type=Path, required=True)
        if name == "seed":
            command.add_argument("--episode-dir", type=Path, required=True)
            command.add_argument("--corpus-dir", type=Path, required=True)
        elif name in {"run", "smoke"}:
            command.add_argument("--episode-id", type=UUID, required=True)
            command.add_argument("--snapshot-id", type=UUID)
            command.add_argument("--question", default="Which service should be checked first?")
            command.add_argument("--symptom-service-key")
            command.add_argument(
                "--ranking-mode",
                choices=tuple(item.value for item in RankingMode),
                default=RankingMode.HEURISTIC.value,
            )
            command.add_argument(
                "--generation-mode",
                choices=tuple(item.value for item in GenerationMode),
                default=GenerationMode.TEMPLATE.value,
            )
            command.add_argument("--ollama-url", default="http://127.0.0.1:11434")
            command.add_argument(
                "--operator-token-file", type=Path, default=Path(".secrets/operator_token")
            )
            if name == "run":
                command.add_argument("--as-of-ns", type=int)
                command.add_argument(
                    "--retrieval-mode",
                    choices=tuple(item.value for item in RetrievalMode),
                    default="GRAPH",
                )
        else:
            command.add_argument("--investigation-id", type=UUID, required=True)
            if name == "evidence":
                command.add_argument("--evidence-id", required=True)
            if name == "export":
                command.add_argument("--output-file", type=Path, required=True)


def _service(
    args: argparse.Namespace,
    factory: sessionmaker[Session],
    driver: Driver | None = None,
) -> InvestigationService:
    settings = None
    provider = None
    prompt_hash = None
    if getattr(args, "generation_mode", GenerationMode.TEMPLATE.value) == GenerationMode.OLLAMA:
        token_file = args.operator_token_file
        if token_file.is_symlink() or not token_file.is_file():
            raise ValueError("local operator token file is missing or unsafe")
        token = token_file.read_text().strip()
        if not token:
            raise ValueError("local operator token is empty")
        settings = RuntimeSettings(
            public_read_only=False,
            generation_provider=GenerationMode.OLLAMA,
            enable_local_model=True,
            operator_token=SecretStr(token),
            ollama_model_digest=PINNED_MANIFEST_DIGEST,
            ollama_url=args.ollama_url,
        )
        provider = OllamaProvider(
            settings,
            prompt_root=Path.cwd(),
            slot_path=args.artifact_root / "generation.lock",
        )
        prompt_hash = provider.prompt_hash
    return InvestigationService(
        factory,
        driver,
        ArtifactRepository(args.artifact_root),
        args.embedding_cache_root or args.artifact_root / "embedding-cache",
        generation_provider=provider,
        generation_settings=settings,
        generation_prompt_hash=prompt_hash,
    )


def _run_request(
    service: InvestigationService,
    args: argparse.Namespace,
    cutoff: int,
    retrieval_mode: RetrievalMode,
) -> dict[str, Any]:
    if cutoff <= 0 or cutoff % 1000:
        raise ValueError("cutoff must be a positive microsecond-aligned epoch nanosecond value")
    request = InvestigationRequest(
        episode_id=args.episode_id,
        snapshot_id=args.snapshot_id,
        as_of=ns_datetime(cutoff),
        temporal_mode=TemporalMode.EVENT,
        question=args.question,
        symptom_service_id=args.symptom_service_key,
        retrieval_mode=retrieval_mode,
        ranking_mode=RankingMode(args.ranking_mode),
        generation_mode=GenerationMode(args.generation_mode),
    )
    return service.investigate(args.workspace_id, request).model_dump(mode="json")


def run_investigation(args: argparse.Namespace) -> int:
    try:
        if not 1 <= args.db_port <= 65535:
            raise ValueError("invalid database port")
        if not args.artifact_root.is_absolute():
            args.artifact_root = Path.cwd() / args.artifact_root
        if args.embedding_cache_root is not None and not args.embedding_cache_root.is_absolute():
            args.embedding_cache_root = Path.cwd() / args.embedding_cache_root
        engine = make_engine(
            database_url(
                host=args.db_host,
                port=args.db_port,
                database=args.db_name,
                user=args.db_user,
                password_file=args.password_file,
            )
        )
        try:
            factory = sessions(engine)
            artifacts = ArtifactRepository(args.artifact_root)
            if args.investigation_action == "seed":
                manifest = EpisodeManifest.model_validate_json(
                    (args.episode_dir / "manifest.json").read_bytes()
                )
                if manifest.workspace_id != args.workspace_id or manifest.baseline_interval is None:
                    raise ValueError("episode lacks a scoped baseline interval")
                submission = submit_import(
                    factory,
                    artifacts,
                    args.episode_dir,
                    idempotency_key=f"seed-{manifest.episode_id}",
                )
                worker = ImportWorker(factory, artifacts, "investigation-seed")
                for _ in range(20):
                    with factory() as session:
                        job = session.get(JobRow, submission.job_id)
                        if job is not None and job.status == "SUCCEEDED":
                            break
                        if job is not None and job.status in {"FAILED", "CANCELLED"}:
                            raise ValueError("seed import did not succeed")
                    worker.run_once()
                else:
                    raise ValueError("seed import was not completed within worker bound")
                ingested = ingest_corpus(factory, artifacts, args.corpus_dir)
                start = closed_analysis_boundary(_ns(manifest.baseline_interval.end))
                late = _ns(manifest.event_interval.end)
                early = min(late - 10_000_000_000, start + 60_000_000_000)
                if early <= start:
                    raise ValueError("episode lacks two closed analysis cutoffs")
                for cutoff in (early, late):
                    materialize_signals(
                        factory,
                        artifacts,
                        workspace_id=args.workspace_id,
                        episode_id=manifest.episode_id,
                        investigation_start_ns=start,
                        as_of_ns=cutoff,
                    )
                with driver_from_auth_file(args.neo4j_uri, args.neo4j_auth_file) as driver:
                    snapshot = build_snapshot(
                        factory,
                        driver,
                        workspace_id=args.workspace_id,
                        episode_id=manifest.episode_id,
                        artifacts=artifacts,
                    )
                output: object = {
                    "episode_id": str(manifest.episode_id),
                    "snapshot_id": str(snapshot.snapshot_id),
                    "early_cutoff_ns": early,
                    "late_cutoff_ns": late,
                    "documents": ingested.documents,
                    "observations": snapshot.observations,
                }
            elif args.investigation_action in {"run", "smoke"}:
                with factory() as session:
                    episode = session.get(EpisodeRow, args.episode_id)
                    if episode is None or episode.workspace_id != args.workspace_id:
                        raise ValueError("episode is missing or outside scope")
                    late = _ns(episode.event_end)
                    analysis_start = episode.analysis_start_ns
                with driver_from_auth_file(args.neo4j_uri, args.neo4j_auth_file) as driver:
                    service = _service(args, factory, driver)
                    if args.investigation_action == "run":
                        cutoff = args.as_of_ns if args.as_of_ns is not None else late
                        output = _run_request(
                            service, args, cutoff, RetrievalMode(args.retrieval_mode)
                        )
                    else:
                        if analysis_start is None:
                            raise ValueError("episode lacks analysis start")
                        early = min(late - 10_000_000_000, analysis_start + 60_000_000_000)
                        summaries = []
                        for cutoff in (early, late):
                            result = _run_request(service, args, cutoff, RetrievalMode.GRAPH)
                            for item in result["evidence"]:
                                service.evidence(
                                    args.workspace_id,
                                    UUID(result["investigation_id"]),
                                    item["evidence_id"],
                                )
                            summaries.append(
                                {
                                    "cutoff_ns": cutoff,
                                    "investigation_id": result["investigation_id"],
                                    "status": result["narrative"]["status"],
                                    "citations_resolved": len(result["evidence"]),
                                    "paths": len(result["paths"]),
                                }
                            )
                        output = {"episodes_checked": 1, "cutoffs": summaries}
            elif args.investigation_action == "show":
                output = (
                    _service(args, factory)
                    .show(args.workspace_id, args.investigation_id)
                    .model_dump(mode="json")
                )
            elif args.investigation_action == "evidence":
                with driver_from_auth_file(args.neo4j_uri, args.neo4j_auth_file) as driver:
                    output = _service(args, factory, driver).evidence(
                        args.workspace_id, args.investigation_id, args.evidence_id
                    )
            elif args.investigation_action == "export":
                exported_result = _service(args, factory).show(
                    args.workspace_id, args.investigation_id
                )
                if args.output_file.is_symlink() or args.output_file.exists():
                    raise ValueError("export destination already exists or is unsafe")
                with args.output_file.open("x", encoding="utf-8") as output_file:
                    output_file.write(exported_result.model_dump_json(indent=2) + "\n")
                output = {
                    "output_file": str(args.output_file),
                    "investigation_id": str(exported_result.investigation_id),
                }
            else:
                raise ValueError("unsupported investigation command")
        finally:
            engine.dispose()
        print(json.dumps(output, sort_keys=True, default=str))
        return 0
    except (ValueError, OSError, SQLAlchemyError, Neo4jError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
