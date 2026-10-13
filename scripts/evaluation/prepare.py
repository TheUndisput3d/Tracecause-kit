"""Prepare public-only evaluation evidence using actual PostgreSQL and Neo4j."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from tracecause.contracts.models import EpisodeManifest
from tracecause.documents.persistence import ingest_corpus
from tracecause.graph.projection import build_snapshot
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.jobs.imports import ImportWorker, submit_import
from tracecause.retrieval.engine import RetrievalRequest, corpus_from_reader
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import (
    closed_analysis_boundary,
    database_url,
    make_engine,
    sessions,
)
from tracecause.storage.schema import JobRow, SnapshotRow, SpanRow
from tracecause.telemetry.persistence import compute_persisted_signals, materialize_signals

from .questions import ROOT

PREPARATION_VERSION = "public-db-evidence-v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publication alone does not guarantee bytes survive host power loss.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, sort_keys=True, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def selected_inputs(public_root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if public_root.is_symlink() or "gold" in public_root.resolve().parts:
        raise ValueError("evaluation preparation requires a public source directory")
    index = json.loads((public_root / "index.json").read_bytes())
    rows = sorted(index["episodes"], key=lambda row: row["episode_id"])
    if len({row["episode_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate public evaluation episode")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = rows[:limit]
    for row in rows:
        episode_id = str(UUID(row["episode_id"]))
        directory = public_root / episode_id
        if any(
            path.is_symlink()
            for path in (directory, directory / "manifest.json", directory / "traces.jsonl")
        ):
            raise ValueError("unsafe evaluation source")
        if digest(directory / "manifest.json") != row["manifest_hash"]:
            raise ValueError("evaluation public manifest hash mismatch")
        manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
        if manifest.episode_id != UUID(episode_id) or len(manifest.sources) != 1:
            raise ValueError("evaluation public identity mismatch")
        if (
            manifest.sources[0].logical_name != "traces.jsonl"
            or digest(directory / "traces.jsonl") != row["source_hash"]
            or manifest.sources[0].sha256 != row["source_hash"]
        ):
            raise ValueError("evaluation public source hash mismatch")
    return rows


def prepare(
    public_root: Path,
    output_root: Path,
    artifact_root: Path,
    limit: int | None,
    database_name: str = "tracecause_evaluation_v1",
    cutoff_after_baseline_seconds: int | None = None,
) -> dict[str, Any]:
    # No gold path or label argument exists on this side of the evaluation boundary.
    if cutoff_after_baseline_seconds is not None and cutoff_after_baseline_seconds < 60:
        raise ValueError("evaluation cutoff must include a complete analysis window")
    rows = selected_inputs(public_root, limit)
    corpus_root = ROOT / "data/runbooks/demo"
    corpus_hash = digest(corpus_root / "manifest.json")
    preparation_hash = digest(Path(__file__))
    artifacts = ArtifactRepository(artifact_root)
    engine = make_engine(
        database_url(
            host="127.0.0.1",
            port=15432,
            database=database_name,
            user="postgres",
            password_file=ROOT / ".secrets/postgres_admin_password",
        )
    )
    factory = sessions(engine)
    output_root.mkdir(parents=True, exist_ok=True)
    completed = []
    try:
        ingest_corpus(factory, artifacts, corpus_root)
        with driver_from_auth_file(
            "bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth"
        ) as driver:
            reader = GraphReader(factory, driver)
            for row in rows:
                started = time.perf_counter()
                episode_id = UUID(row["episode_id"])
                directory = public_root / str(episode_id)
                path = output_root / f"{episode_id}.json"
                identity = {
                    **row,
                    "corpus_hash": corpus_hash,
                    "preparation_hash": preparation_hash,
                    "preparation_version": PREPARATION_VERSION,
                    "database_name": database_name,
                    "cutoff_after_baseline_seconds": cutoff_after_baseline_seconds,
                }
                if path.exists():
                    saved = json.loads(path.read_bytes())
                    if saved["identity"] != identity:
                        raise ValueError(
                            "existing prepared evidence has different inputs; use a new output root"
                        )
                    with factory() as session:
                        snapshot = session.get(SnapshotRow, UUID(saved["snapshot_id"]))
                        if (
                            snapshot is None
                            or snapshot.status != "READY"
                            or snapshot.content_hash != saved["snapshot_hash"]
                        ):
                            raise ValueError("prepared snapshot is missing or changed")
                    completed.append({"episode_id": str(episode_id), "sha256": digest(path)})
                    continue
                manifest = EpisodeManifest.model_validate_json(
                    (directory / "manifest.json").read_bytes()
                )
                if manifest.baseline_interval is None:
                    raise ValueError("evaluation episode lacks baseline")
                from scripts.capture.validation import timestamp_ns

                start = closed_analysis_boundary(
                    timestamp_ns(manifest.baseline_interval.end.isoformat())
                )
                end = timestamp_ns(manifest.event_interval.end.isoformat())
                cutoff = (
                    end
                    if cutoff_after_baseline_seconds is None
                    else start + cutoff_after_baseline_seconds * 1_000_000_000
                )
                if cutoff > end:
                    raise ValueError("evaluation cutoff exceeds public episode interval")
                submission = submit_import(
                    factory, artifacts, directory, idempotency_key=f"evaluation-public-{episode_id}"
                )
                worker = ImportWorker(factory, artifacts, "evaluation-prepare")
                for _ in range(20):
                    with factory() as session:
                        job = session.get(JobRow, submission.job_id)
                        if job is not None and job.status == "SUCCEEDED":
                            break
                        if job is not None and job.status in {"FAILED", "CANCELLED"}:
                            raise ValueError("evaluation import failed")
                    worker.run_once()
                else:
                    raise ValueError("evaluation import did not finish within worker bound")
                materialize_signals(
                    factory,
                    artifacts,
                    workspace_id=manifest.workspace_id,
                    episode_id=episode_id,
                    investigation_start_ns=start,
                    as_of_ns=cutoff,
                )
                snapshot = build_snapshot(
                    factory,
                    driver,
                    workspace_id=manifest.workspace_id,
                    episode_id=episode_id,
                    artifacts=artifacts,
                )
                report = compute_persisted_signals(
                    factory,
                    workspace_id=manifest.workspace_id,
                    episode_id=episode_id,
                    as_of_ns=cutoff,
                )
                request = RetrievalRequest(
                    manifest.workspace_id,
                    episode_id,
                    snapshot.snapshot_id,
                    cutoff,
                    "event",
                    "Which service should be checked first?",
                )
                corpus = corpus_from_reader(reader, artifacts, request)
                calls, truncated = reader.eligible_calls_bounded(
                    workspace_id=manifest.workspace_id,
                    episode_id=episode_id,
                    snapshot_id=snapshot.snapshot_id,
                    as_of_ns=cutoff,
                    mode="event",
                )
                with factory() as session:
                    names = dict(
                        session.execute(
                            select(SpanRow.service_name, SpanRow.service_key)
                            .where(
                                SpanRow.workspace_id == manifest.workspace_id,
                                SpanRow.episode_id == episode_id,
                            )
                            .distinct()
                        ).all()
                    )
                result = {
                    "identity": identity,
                    "workspace_id": str(manifest.workspace_id),
                    "episode_id": str(episode_id),
                    "origin": manifest.origin,
                    "snapshot_id": str(snapshot.snapshot_id),
                    "snapshot_hash": snapshot.content_hash,
                    "as_of_ns": cutoff,
                    "service_keys": names,
                    "signal_report": asdict(report),
                    "corpus": [asdict(item) for item in corpus],
                    "calls": [asdict(item) for item in calls],
                    "calls_truncated": truncated,
                    "preparation_seconds": time.perf_counter() - started,
                }
                write_json(path, result)
                completed.append({"episode_id": str(episode_id), "sha256": digest(path)})
                print(
                    json.dumps(
                        {
                            "prepared": len(completed),
                            "total": len(rows),
                            "episode_id": str(episode_id),
                            "evidence_items": len(corpus),
                        }
                    ),
                    flush=True,
                )
                write_json(
                    output_root / "progress.json", {"status": "preparing", "episodes": completed}
                )
    finally:
        engine.dispose()
    result = {
        "status": "prepared_public_evidence",
        "preparation_version": PREPARATION_VERSION,
        "public_index_hash": digest(public_root / "index.json"),
        "corpus_hash": corpus_hash,
        "preparation_hash": preparation_hash,
        "episodes": completed,
        "gold_access": False,
        "partial_selection": limit is not None,
        "cutoff_after_baseline_seconds": cutoff_after_baseline_seconds,
    }
    write_json(output_root / "index.json", result)
    return {key: value for key, value in result.items() if key != "episodes"} | {
        "episodes": len(completed)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=ROOT / "data/generated/seed-20261010")
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/phase13-prepared-v2")
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "reports/phase13-artifacts")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--database-name", default="tracecause_evaluation_v1")
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.public_root,
                args.output_root,
                args.artifact_root,
                args.limit,
                args.database_name,
            )
        )
    )


if __name__ == "__main__":
    main()
