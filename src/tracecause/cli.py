"""Phase 00 command-line entry point and dependency checks."""

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from neo4j import GraphDatabase
from psycopg import connect

from tracecause.cli_documents import add_document_commands, run_documents
from tracecause.cli_graph import add_graph_commands, run_graph
from tracecause.cli_investigation import add_investigation_commands, run_investigation
from tracecause.cli_models import add_model_commands, run_models
from tracecause.cli_ranking import add_ranking_commands, run_ranking
from tracecause.cli_retrieval import add_retrieval_commands, run_retrieval
from tracecause.cli_storage import add_storage_commands, run_storage
from tracecause.contracts.canonical import bytes_sha256
from tracecause.contracts.models import EpisodeManifest, ImportDiagnostic, ImportReport
from tracecause.ingestion.normalize import normalize_otlp, serialized_span
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ingestion.redaction import REDACTION_VERSION
from tracecause.ingestion.source_allowlist import resolve_episode_sources


def _check_postgres() -> str:
    dsn = os.environ.get("TRACECAUSE_PG_DSN")
    if not dsn:
        raise ValueError("TRACECAUSE_PG_DSN is required for database checks")
    with connect(dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
    if result != (1,):
        raise RuntimeError("PostgreSQL probe returned an unexpected result")
    return "ok"


def _check_neo4j() -> str:
    uri = os.environ.get("TRACECAUSE_NEO4J_URI")
    user = os.environ.get("TRACECAUSE_NEO4J_USER")
    password = os.environ.get("TRACECAUSE_NEO4J_PASSWORD")
    if not uri or not user or not password:
        raise ValueError("TRACECAUSE_NEO4J_URI, USER and PASSWORD are required")
    with GraphDatabase.driver(uri, auth=(user, password), connection_timeout=5) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run("RETURN 1 AS value").single(strict=True)
    if result is None or result["value"] != 1:
        raise RuntimeError("Neo4j probe returned an unexpected result")
    return "ok"


def _normalize_command(
    episode_dir: Path, artifact_root: Path, output_dir: Path, allow_partial: bool
) -> int:
    if not artifact_root.is_absolute() or artifact_root == Path("/"):
        raise ValueError("invalid_artifact_root")
    if any(path.is_symlink() for path in (artifact_root, *artifact_root.parents)):
        raise ValueError("unsafe_artifact_root")
    if output_dir.is_absolute() or not output_dir.parts or ".." in output_dir.parts:
        raise ValueError("invalid_output_dir")
    root = artifact_root.resolve()
    destination = root / output_dir
    cursor = root
    for part in output_dir.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("unsafe_output_dir")
    if destination.exists():
        raise ValueError("output_already_exists")
    manifest_file = episode_dir / "manifest.json"
    if (
        "gold" in episode_dir.resolve().parts
        or any(path.is_symlink() for path in (episode_dir, *episode_dir.parents))
        or manifest_file.is_symlink()
    ):
        raise ValueError("unsafe_episode_dir")
    manifest_bytes = manifest_file.read_bytes()
    manifest = EpisodeManifest.model_validate_json(manifest_bytes)
    sources = resolve_episode_sources(episode_dir, manifest)
    if len(sources) != 1 or manifest.sources[0].kind not in {"otlp_json", "otlp_jsonl"}:
        raise ValueError("unsupported_source_set")
    source = manifest.sources[0]
    encoding = (
        InputEncoding.OTLP_JSONL_V1 if source.kind == "otlp_jsonl" else InputEncoding.OTLP_JSON_V1
    )
    with sources[0].open("rb") as reader:
        batch = normalize_otlp(
            reader,
            encoding=encoding,
            workspace_id=manifest.workspace_id,
            episode_id=manifest.episode_id,
            expected_source_hash=source.sha256,
            source_name=source.logical_name,
        )
    size_mismatch = sources[0].stat().st_size != source.bytes
    failed = batch.fatal or size_mismatch or (not allow_partial and batch.rejected_count > 0)
    status = "failed" if failed else "partial" if batch.rejected_count else "accepted"
    diagnostics = list(batch.diagnostics)
    if size_mismatch and len(diagnostics) < 20:
        diagnostics.append(
            ImportDiagnostic(
                code="source_size_mismatch",
                source_name=source.logical_name,
                record_offset=0,
                safe_message="source size mismatch",
            )
        )
    report = ImportReport(
        workspace_id=manifest.workspace_id,
        episode_id=manifest.episode_id,
        source_manifest_hash=bytes_sha256(manifest_bytes),
        accepted_count=0 if failed else batch.accepted_count,
        rejected_count=batch.rejected_count,
        duplicate_count=batch.duplicate_count,
        conflict_count=batch.conflict_count,
        diagnostics=diagnostics,
    )
    destination.mkdir(parents=True, exist_ok=False)
    if not failed:
        with (destination / "spans.jsonl").open("wb") as output:
            for span in batch.spans:
                output.write(serialized_span(span))
    (destination / "report.json").write_text(
        json.dumps(
            {
                "status": status,
                "encoding": encoding.value,
                "redaction_version": REDACTION_VERSION,
                "source_hash": batch.source_hash,
                "redacted_hash": batch.redacted_hash if not failed else None,
                "report": report.model_dump(mode="json"),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"status": status, "output_dir": str(destination)}, sort_keys=True))
    return 2 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracecause")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="show runtime and optionally probe databases")
    doctor.add_argument("--check-databases", action="store_true")
    normalize = subparsers.add_parser("normalize", help="validate and redact an OTLP episode")
    normalize.add_argument("--episode-dir", type=Path, required=True)
    normalize.add_argument("--artifact-root", type=Path, required=True)
    normalize.add_argument("--output-dir", type=Path, required=True)
    normalize.add_argument("--allow-partial", action="store_true")
    add_storage_commands(subparsers)
    add_graph_commands(subparsers)
    add_document_commands(subparsers)
    add_retrieval_commands(subparsers)
    add_investigation_commands(subparsers)
    add_model_commands(subparsers)
    add_ranking_commands(subparsers)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        report: dict[str, str] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "phase": "00-foundation",
        }
        if args.check_databases:
            try:
                report["postgres"] = _check_postgres()
                report["neo4j"] = _check_neo4j()
            except Exception as exc:
                report["error"] = f"database probe failed ({type(exc).__name__})"
                print(json.dumps(report, sort_keys=True), file=sys.stderr)
                return 1
        print(json.dumps(report, sort_keys=True))
        return 0
    if args.command == "normalize":
        try:
            return _normalize_command(
                args.episode_dir, args.artifact_root, args.output_dir, args.allow_partial
            )
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
            return 2
    if args.command in {"db", "import", "job", "worker", "signals"}:
        return run_storage(args)
    if args.command == "graph":
        return run_graph(args)
    if args.command == "documents":
        return run_documents(args)
    if args.command == "retrieval":
        return run_retrieval(args)
    if args.command == "investigation":
        return run_investigation(args)
    if args.command == "models":
        return run_models(args)
    if args.command == "ranking":
        return run_ranking(args)
    return 2
