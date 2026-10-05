"""Operator CLI for migrations, durable imports, jobs and recovery inventory."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tracecause.jobs.imports import ImportValidationError, ImportWorker, submit_import
from tracecause.jobs.queue import JobQueue
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.maintenance import (
    backup_bytes,
    delete_unreferenced_artifact,
    reconciliation_inventory,
)
from tracecause.storage.postgres import (
    DEFAULT_WORKSPACE_ID,
    database_url,
    health,
    make_engine,
    sessions,
)
from tracecause.storage.schema import JobRow
from tracecause.telemetry.persistence import inspect_signals, materialize_signals

ROOT = Path(__file__).resolve().parents[2]


def _connection_args(parser: argparse.ArgumentParser, *, admin: bool = False) -> None:
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=15432)
    parser.add_argument("--db-name", default="tracecause")
    parser.add_argument("--db-user", default="postgres" if admin else "tracecause_app")
    parser.add_argument("--password-file", type=Path, required=True)


def add_storage_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    db = subparsers.add_parser("db", help="database migration, health and maintenance")
    db_sub = db.add_subparsers(dest="db_action", required=True)
    migrate = db_sub.add_parser("migrate")
    _connection_args(migrate, admin=True)
    health_cmd = db_sub.add_parser("health")
    _connection_args(health_cmd)
    backup = db_sub.add_parser("backup-metadata")
    _connection_args(backup)
    backup.add_argument("--artifact-root", type=Path, required=True)
    reconcile = db_sub.add_parser("reconcile")
    _connection_args(reconcile)
    reconcile.add_argument("--artifact-root", type=Path, required=True)
    cleanup = db_sub.add_parser("cleanup")
    _connection_args(cleanup)
    cleanup.add_argument("--artifact-root", type=Path, required=True)
    cleanup.add_argument("--artifact-hash")
    cleanup.add_argument("--apply", action="store_true")

    import_parser = subparsers.add_parser("import", help="stage a redacted episode import")
    import_sub = import_parser.add_subparsers(dest="import_action", required=True)
    submit = import_sub.add_parser("submit")
    _connection_args(submit)
    submit.add_argument("--episode-dir", type=Path, required=True)
    submit.add_argument("--artifact-root", type=Path, required=True)
    submit.add_argument("--idempotency-key", required=True)
    submit.add_argument("--allow-partial", action="store_true")

    job = subparsers.add_parser("job", help="inspect or cancel a durable job")
    job_sub = job.add_subparsers(dest="job_action", required=True)
    show = job_sub.add_parser("show")
    _connection_args(show)
    show.add_argument("--job-id", type=UUID, required=True)
    cancel = job_sub.add_parser("cancel")
    _connection_args(cancel)
    cancel.add_argument("--job-id", type=UUID, required=True)
    retry = job_sub.add_parser("retry")
    _connection_args(retry)
    retry.add_argument("--job-id", type=UUID, required=True)

    worker = subparsers.add_parser("worker", help="run the PostgreSQL lease worker")
    worker_sub = worker.add_subparsers(dest="worker_action", required=True)
    run = worker_sub.add_parser("run")
    _connection_args(run)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--once", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=0.5)

    signals = subparsers.add_parser("signals", help="materialize or inspect trace-derived signals")
    signal_sub = signals.add_subparsers(dest="signal_action", required=True)
    materialize = signal_sub.add_parser("materialize")
    _connection_args(materialize)
    materialize.add_argument("--artifact-root", type=Path, required=True)
    materialize.add_argument("--episode-id", type=UUID, required=True)
    materialize.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)
    materialize.add_argument("--investigation-start-ns", type=int, required=True)
    materialize.add_argument("--as-of-ns", type=int, required=True)
    materialize.add_argument("--mode", choices=("event", "available"), default="event")
    inspect = signal_sub.add_parser("inspect")
    _connection_args(inspect)
    inspect.add_argument("--artifact-root", type=Path, required=True)
    inspect.add_argument("--episode-id", type=UUID, required=True)
    inspect.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)
    inspect.add_argument("--report-hash", required=True)
    inspect.add_argument("--observation-id", type=UUID)


def run_storage(args: argparse.Namespace) -> int:
    try:
        if not 1 <= args.db_port <= 65535:
            raise ValueError("invalid database port")
        url = database_url(
            host=args.db_host,
            port=args.db_port,
            database=args.db_name,
            user=args.db_user,
            password_file=args.password_file,
        )
        engine = make_engine(url)
        try:
            session_factory = sessions(engine)
            if args.command == "db":
                if args.db_action == "migrate":
                    config = Config(str(ROOT / "alembic.ini"))
                    with engine.begin() as connection:
                        config.attributes["connection"] = connection
                        command.upgrade(config, "head")
                    print(json.dumps({"migration": "head", "status": "ok"}, sort_keys=True))
                    return 0
                if args.db_action == "health":
                    healthy = health(engine)
                    print(json.dumps({"postgres": "ok" if healthy else "failed"}))
                    return 0 if healthy else 2
                artifacts = ArtifactRepository(args.artifact_root)
                if args.db_action == "backup-metadata":
                    with session_factory() as session:
                        descriptor = artifacts.put_bytes(backup_bytes(session))
                    print(
                        json.dumps(
                            {"metadata_artifact_hash": descriptor.content_hash}, sort_keys=True
                        )
                    )
                    return 0
                if args.db_action == "reconcile":
                    print(
                        json.dumps(
                            reconciliation_inventory(session_factory, artifacts), sort_keys=True
                        )
                    )
                    return 0
                if args.db_action == "cleanup":
                    deleted = None
                    if args.artifact_hash:
                        deleted = delete_unreferenced_artifact(
                            session_factory,
                            artifacts,
                            args.artifact_hash,
                            dry_run=not args.apply,
                        )
                    temporary = artifacts.cleanup_temporary(dry_run=not args.apply)
                    print(
                        json.dumps(
                            {
                                "dry_run": not args.apply,
                                "artifact_deleted": deleted,
                                "temporary_files": temporary,
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
            if args.command == "import":
                artifacts = ArtifactRepository(args.artifact_root)
                submission = submit_import(
                    session_factory,
                    artifacts,
                    args.episode_dir,
                    idempotency_key=args.idempotency_key,
                    allow_partial=args.allow_partial,
                )
                print(
                    json.dumps(
                        {
                            "job_id": str(submission.job_id),
                            "episode_id": str(submission.episode_id),
                            "status": submission.status,
                            "accepted_count": submission.accepted_count,
                            "rejected_count": submission.rejected_count,
                            "duplicate_count": submission.duplicate_count,
                            "diagnostics": [
                                item.model_dump(mode="json") for item in submission.diagnostics
                            ],
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if args.command == "job":
                queue = JobQueue(session_factory)
                if args.job_action == "cancel":
                    print(json.dumps({"status": queue.cancel(args.job_id)}, sort_keys=True))
                    return 0
                if args.job_action == "retry":
                    print(json.dumps({"status": queue.retry(args.job_id)}, sort_keys=True))
                    return 0
                with session_factory() as session:
                    row = session.scalar(select(JobRow).where(JobRow.id == args.job_id))
                    if row is None:
                        raise ValueError("job not found")
                    print(
                        json.dumps(
                            {
                                "job_id": str(row.id),
                                "episode_id": str(row.episode_id) if row.episode_id else None,
                                "status": row.status,
                                "attempts": row.attempts,
                                "checkpoint": row.checkpoint,
                                "safe_error": row.safe_error,
                                "import_diagnostics": row.payload.get("diagnostics", []),
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
            if args.command == "worker":
                if not 0.1 <= args.poll_seconds <= 10:
                    raise ValueError("invalid poll interval")
                worker = ImportWorker(
                    session_factory,
                    ArtifactRepository(args.artifact_root),
                    f"worker-{uuid4()}",
                )
                while True:
                    processed = worker.run_once()
                    if processed is not None:
                        print(json.dumps({"processed_job_id": str(processed)}, sort_keys=True))
                    if args.once:
                        return 0
                    if processed is None:
                        time.sleep(args.poll_seconds)
            if args.command == "signals":
                artifacts = ArtifactRepository(args.artifact_root)
                if args.signal_action == "materialize":
                    materialized = materialize_signals(
                        session_factory,
                        artifacts,
                        workspace_id=args.workspace_id,
                        episode_id=args.episode_id,
                        investigation_start_ns=args.investigation_start_ns,
                        as_of_ns=args.as_of_ns,
                        mode=args.mode,
                    )
                    print(json.dumps(materialized.__dict__, sort_keys=True, default=str))
                    return 0
                inspected = inspect_signals(
                    session_factory,
                    artifacts,
                    workspace_id=args.workspace_id,
                    episode_id=args.episode_id,
                    report_hash=args.report_hash,
                    observation_id=args.observation_id,
                )
                print(json.dumps(inspected, sort_keys=True))
                return 0
            raise ValueError("unsupported storage command")
        finally:
            engine.dispose()
    except ImportValidationError as exc:
        print(
            json.dumps(
                {
                    "error": exc.code,
                    "rejected_count": exc.rejected_count,
                    "diagnostics": [item.model_dump(mode="json") for item in exc.diagnostics],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError, SQLAlchemyError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
