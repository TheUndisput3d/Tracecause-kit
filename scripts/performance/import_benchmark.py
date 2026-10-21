"""Deterministic synthetic span import with real PostgreSQL and measured stages."""

import argparse
import hashlib
import json
import os
import platform
import resource
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from psycopg import connect, sql
from sqlalchemy import event, func, select

from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import EpisodeManifest
from tracecause.jobs.imports import ImportWorker, submit_import
from tracecause.storage.schema import EpisodeRow, JobRow, SpanRow

BASE_NS = 1767225600000000000


def generate(root: Path, count: int, run_id: str) -> EpisodeManifest:
    if count < 5 or count % 5:
        raise ValueError("span count must be a positive multiple of five")
    root.mkdir(parents=True)
    traces = count // 5
    sources = []
    # Four files preserve the actual25MiB per-source boundary at100k spans.
    for shard in range(4):
        path = root / f"traces-{shard}.jsonl"
        with path.open("wb") as output:
            for trace in range(shard, traces, 4):
                start = BASE_NS + trace * (590_000_000_000 // traces)
                resources = []
                for service in range(5):
                    span = {
                        "traceId": f"{trace + 1:032x}",
                        "spanId": f"{service + 1:016x}",
                        "name": "GET /synthetic-load",
                        "kind": 2,
                        "startTimeUnixNano": str(start + service * 1_000_000),
                        "endTimeUnixNano": str(start + (20 - service) * 1_000_000),
                        "status": {"code": 1},
                    }
                    if service:
                        span["parentSpanId"] = f"{service:016x}"
                    resources.append(
                        {
                            "resource": {
                                "attributes": [
                                    {
                                        "key": "service.name",
                                        "value": {"stringValue": f"load-{service}"},
                                    },
                                    {
                                        "key": "service.namespace",
                                        "value": {"stringValue": "performance"},
                                    },
                                    {
                                        "key": "deployment.environment",
                                        "value": {"stringValue": "synthetic"},
                                    },
                                ]
                            },
                            "scopeSpans": [
                                {"scope": {"name": "tracecause-performance-v1"}, "spans": [span]}
                            ],
                        }
                    )
                output.write(
                    json.dumps({"resourceSpans": resources}, separators=(",", ":")).encode() + b"\n"
                )
        raw = path.read_bytes()
        if len(raw) > 25 * 1024 * 1024:
            raise ValueError("performance source exceeds normal source cap")
        sources.append(
            {
                "logical_name": path.name,
                "kind": "otlp_jsonl",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "published_at": None,
            }
        )
    manifest = EpisodeManifest.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": "00000000-0000-4000-8000-000000000001",
            "episode_id": str(uuid5(NAMESPACE_URL, f"tracecause-performance-v1:{count}:{run_id}")),
            "display_name": f"Synthetic performance fixture: {count} spans",
            "origin": "synthetic",
            "event_interval": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z"},
            "baseline_interval": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:05:00Z"},
            "availability_recorded": False,
            "sources": sources,
            "public_notes": "Synthetic load diagnostic; no ranking labels or accuracy claims.",
        }
    )
    (root / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
    return manifest


def run(output: Path, count: int, run_id: str) -> dict[str, object]:
    output.mkdir(parents=True)
    settings = load_settings(Path("configs/defaults.yaml"))
    if settings.pg_user != "postgres" or settings.pg_password is None:
        raise ValueError("local disposable database bootstrap requires explicit admin settings")
    database = "tracecause_perf_" + uuid5(NAMESPACE_URL, str(output.absolute())).hex[:20]
    with connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname="postgres",
        user="postgres",
        password=settings.pg_password.get_secret_value(),
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    settings = settings.model_copy(
        update={"pg_database": database, "artifact_root": (output / "artifacts").absolute()}
    )
    resources = open_resources(settings)
    try:
        assert resources.engine is not None
        with resources.engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        statement_count = 0

        def before(*args, **kwargs):
            nonlocal statement_count
            statement_count += 1

        event.listen(resources.engine, "before_cursor_execute", before)
        manifest = generate(output / "input", count, run_id)
        started = time.perf_counter()
        submission = submit_import(
            resources.factory,
            resources.artifacts,
            output / "input",
            idempotency_key="performance-import",
        )
        normalized_at = time.perf_counter()
        normalization_sql = statement_count
        job_id = ImportWorker(
            resources.factory, resources.artifacts, "performance-worker"
        ).run_once()
        finished = time.perf_counter()
        assert job_id == submission.job_id
        with resources.factory() as session:
            stored = session.scalar(
                select(func.count())
                .select_from(SpanRow)
                .where(SpanRow.episode_id == manifest.episode_id)
            )
            job = session.get(JobRow, job_id)
            episode = session.get(EpisodeRow, manifest.episode_id)
            assert (
                stored == count
                and job.status == "SUCCEEDED"
                and episode.ingestion_status == "IMPORTED"
            )
        result = {
            "kind": "synthetic_import_performance",
            "database": database,
            "spans": stored,
            "sources": [s.model_dump(mode="json") for s in manifest.sources],
            "concurrency": 1,
            "fresh_database": True,
            "cache": "fresh application state; host filesystem cache uncontrolled",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "processor": platform.processor(),
            "timings_seconds": {
                "normalize_stage_artifact": normalized_at - started,
                "worker_sql_persistence": finished - normalized_at,
                "total_import": finished - started,
            },
            "sql_statements": {
                "normalization_staging": normalization_sql,
                "total_including_verification": statement_count,
            },
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "target_seconds": 600,
            "target_met": finished - started < 600,
            "errors": 0,
        }
        (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spans", type=int, default=100_000)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.spans, args.run_id), indent=2))


if __name__ == "__main__":
    main()
