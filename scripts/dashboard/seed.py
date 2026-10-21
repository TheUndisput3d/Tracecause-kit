"""Seed an isolated real dashboard database from public fixtures and safe reports."""

import json
import os
import secrets
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import command
from alembic.config import Config
from psycopg import connect, sql
from sqlalchemy import text

from tracecause.api.publish_report import publish
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import EvaluationReport, InvestigationRequest, RetrievalMode
from tracecause.documents.persistence import ingest_corpus
from tracecause.investigation.ranking import is_anomalous
from tracecause.investigation.templates import ns_datetime
from tracecause.investigation.validation import as_ns
from tracecause.jobs.api_jobs import ApiJobs
from tracecause.jobs.api_worker import ApiWorker
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import EpisodeRow, JobRow
from tracecause.telemetry.persistence import compute_persisted_signals

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "reports/phase15-dashboard"
DATABASE = "tracecause_dashboard_v1"


def environment(*, admin: bool = False) -> dict[str, str]:
    return {
        "TRACECAUSE_PUBLIC_READ_ONLY": "false",
        "TRACECAUSE_OPERATOR_TOKEN_FILE": str(OUTPUT / "operator_token"),
        "TRACECAUSE_PG_HOST": "127.0.0.1",
        "TRACECAUSE_PG_PORT": "15432",
        "TRACECAUSE_PG_DATABASE": DATABASE,
        "TRACECAUSE_PG_USER": "postgres" if admin else "tracecause_app",
        "TRACECAUSE_PG_PASSWORD_FILE": str(
            ROOT / ".secrets" / ("postgres_admin_password" if admin else "postgres_password")
        ),
        "TRACECAUSE_NEO4J_URI": "bolt://127.0.0.1:17687",
        "TRACECAUSE_NEO4J_AUTH_FILE": str(ROOT / ".secrets/neo4j_auth"),
        "TRACECAUSE_ARTIFACT_ROOT": str(OUTPUT / "artifacts"),
        "TRACECAUSE_STAGING_ROOT": str(OUTPUT / "staging"),
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    token_file = OUTPUT / "operator_token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(32) + "\n")
    with connect(
        host="127.0.0.1",
        port=15432,
        dbname="postgres",
        user="postgres",
        password=(ROOT / ".secrets/postgres_admin_password").read_text().strip(),
        autocommit=True,
    ) as connection:
        if (
            connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone()
            is None
        ):
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
    resources = open_resources(
        load_settings(ROOT / "configs/defaults.yaml", environment(admin=True))
    )
    try:
        config = Config(str(ROOT / "alembic.ini"))
        assert resources.engine is not None
        with resources.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            for statement in (
                "GRANT CONNECT ON DATABASE tracecause_dashboard_v1 TO tracecause_app",
                "GRANT USAGE ON SCHEMA public TO tracecause_app",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                "TO tracecause_app",
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tracecause_app",
            ):
                connection.execute(text(statement))
        resources.close()
        resources = open_resources(load_settings(ROOT / "configs/defaults.yaml", environment()))
        ingest_corpus(resources.factory, resources.artifacts, ROOT / "data/runbooks/demo")
        identifiers = []
        for original in (
            "03c9ef66-2609-5aec-86fa-9dda41e1c9af",
            "0afa58e4-aec3-563d-bba1-6fb5fb689a46",
        ):
            identifier = uuid5(NAMESPACE_URL, "tracecause-dashboard-v1:" + original)
            directory = resources.staging / str(identifier)
            if not directory.exists():
                shutil.copytree(ROOT / "data/generated/seed-20261010" / original, directory)
                manifest_path = directory / "manifest.json"
                manifest = json.loads(manifest_path.read_bytes())
                manifest["episode_id"] = str(identifier)
                manifest_path.write_text(json.dumps(manifest))
            manifest = json.loads((directory / "manifest.json").read_bytes())
            job = ApiJobs(resources.factory, resources.settings).submit_import(
                directory, f"seed-{identifier}"
            )
            worker = ApiWorker(resources)
            for _ in range(20):
                with resources.factory() as session:
                    stored = session.get(JobRow, job.id)
                    assert stored is not None
                    if stored.status == "SUCCEEDED":
                        break
                    if stored.status in {"FAILED", "CANCELLED"}:
                        raise RuntimeError(f"seed import failed: {stored.safe_error}")
                worker.run_once()
            else:
                raise RuntimeError("seed import did not finish")
            with resources.factory() as session:
                episode = session.get(EpisodeRow, identifier)
                assert episode is not None and episode.active_snapshot_id is not None
                snapshot = episode.active_snapshot_id
            results = []
            for method in ("LEXICAL", "GRAPH"):
                request = InvestigationRequest.model_validate(
                    {
                        "episode_id": str(identifier),
                        "snapshot_id": str(snapshot),
                        "as_of": manifest["event_interval"]["end"],
                        "temporal_mode": "event_time_replay",
                        "question": (
                            "Which services should be investigated, "
                            "and what evidence supports them?"
                        ),
                        "retrieval_mode": method,
                        "ranking_mode": "heuristic",
                        "generation_mode": "template",
                    }
                )
                queued = ApiJobs(resources.factory, resources.settings).submit_investigation(
                    request, f"seed-{identifier}-{method}"
                )
                with resources.factory() as session:
                    stored = session.get(JobRow, queued.id)
                    assert stored is not None
                    already_done = stored.status == "SUCCEEDED"
                if not already_done:
                    worker.run_once()
                with resources.factory() as session:
                    stored = session.get(JobRow, queued.id)
                    assert stored is not None and stored.status == "SUCCEEDED", (
                        "seed investigation failed"
                    )
                    results.append(str(UUID(stored.payload["result_id"])))
            identifiers.append(
                {
                    "episode_id": str(identifier),
                    "snapshot_id": str(snapshot),
                    "results": results,
                    "cutoff": manifest["event_interval"]["end"],
                }
            )
        # An explicitly selected quiet cursor tests the healthy UI state. The episode's
        # end can contain chance threshold crossings; do not relabel that saved output.
        healthy = identifiers[1]
        with resources.factory() as session:
            episode = session.get(EpisodeRow, UUID(healthy["episode_id"]))
            assert episode is not None and episode.analysis_start_ns is not None
            start, end = episode.analysis_start_ns, as_ns(episode.event_end)
        quiet = None
        for cutoff in range(end, start + 10_000_000_000, -10_000_000_000):
            signals = compute_persisted_signals(
                resources.factory,
                workspace_id=DEFAULT_WORKSPACE_ID,
                episode_id=UUID(healthy["episode_id"]),
                as_of_ns=cutoff,
            )
            if not any(is_anomalous(item) for item in signals.signals):
                quiet = cutoff
                break
        if quiet is None:
            raise RuntimeError("no observed quiet cursor in the healthy-state fixture")
        request = request.model_copy(
            update={"as_of": ns_datetime(quiet), "retrieval_mode": RetrievalMode.LEXICAL}
        )
        request = InvestigationRequest.model_validate(request.model_dump())
        queued = ApiJobs(resources.factory, resources.settings).submit_investigation(
            request, "seed-healthy-cursor"
        )
        with resources.factory() as session:
            stored = session.get(JobRow, queued.id)
            assert stored is not None
            already_done = stored.status == "SUCCEEDED"
        if not already_done:
            ApiWorker(resources).run_once()
        with resources.factory() as session:
            stored = session.get(JobRow, queued.id)
            assert stored is not None and stored.status == "SUCCEEDED"
            healthy_result = stored.payload["result_id"]
        reports = []
        for report_id in (
            "8574b985-f4d2-5e45-b901-93b66bf30fa0",
            "5e9a68df-fa4c-510a-91fd-d8f1db682235",
        ):
            report = EvaluationReport.model_validate_json(
                (ROOT / "data/evaluation/published" / f"{report_id}.json").read_bytes()
            )
            reports.append(str(publish(resources, report).report_id))
        (OUTPUT / "fixture.json").write_text(
            json.dumps(
                {
                    "database": DATABASE,
                    "episodes": identifiers,
                    "reports": reports,
                    "healthy_result": healthy_result,
                    "healthy_cutoff": ns_datetime(quiet).isoformat(),
                },
                indent=2,
            )
            + "\n"
        )
        print(
            json.dumps(
                {"episodes": len(identifiers), "reports": len(reports), "database": DATABASE}
            )
        )
    finally:
        resources.close()


if __name__ == "__main__":
    main()
