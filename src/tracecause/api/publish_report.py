"""Operator CLI for registering sanitized local evaluation aggregates."""

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from tracecause.api.resources import Resources, open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.canonical import canonical_json
from tracecause.contracts.models import EvaluationReport
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, StorageConflict
from tracecause.storage.schema import EvaluationReportRow, SourceArtifactRow, WorkspaceRow

METRIC_KEY = re.compile(
    r"^canonical\.(train|dev|test)\.(anomaly|dense|hybrid|graph_heuristic|graph_learned)\.[a-z0-9_.]+$"
)


def publish(resources: Resources, report: EvaluationReport) -> EvaluationReport:
    if resources.settings.public_read_only:
        raise ValueError("local report registration requires operator mode")
    if not report.metrics or any(not METRIC_KEY.fullmatch(key) for key in report.metrics):
        raise ValueError("only sanitized canonical aggregate metrics can be registered")
    if report.model_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", report.model_digest):
        raise ValueError("report model digest is not a reviewed SHA-256")
    with resources.factory.begin() as session:
        existing = session.get(EvaluationReportRow, report.report_id)
        if existing:
            previous = EvaluationReport.model_validate_json(
                resources.artifacts.get_bytes(existing.artifact_hash)
            )
            if previous != report.model_copy(update={"published_at": previous.published_at}):
                raise StorageConflict("published report is immutable")
            return previous
        published = report.model_copy(update={"published_at": datetime.now(UTC)})
        descriptor = resources.artifacts.put_bytes(canonical_json(published) + b"\n")
        session.execute(
            insert(WorkspaceRow)
            .values(id=DEFAULT_WORKSPACE_ID, name="default")
            .on_conflict_do_nothing()
        )
        session.add(
            SourceArtifactRow(
                content_hash=descriptor.content_hash,
                workspace_id=DEFAULT_WORKSPACE_ID,
                kind="public_evaluation_report",
                byte_size=descriptor.byte_size,
                relative_storage_key=descriptor.relative_key,
                schema_version="1.0",
                redaction_version="aggregate-only-v1",
            )
        )
        session.flush()
        session.add(
            EvaluationReportRow(
                id=published.report_id,
                artifact_hash=descriptor.content_hash,
                private_run_reference=None,
                dataset_hash=published.dataset_hash,
                split_hash=published.split_hash,
                model_hash=published.model_digest or "none",
                config_hash=published.config_hash,
                status="PUBLISHED",
            )
        )
    return published


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = EvaluationReport.model_validate_json(args.report.read_bytes())
    resources = open_resources(load_settings(Path("configs/defaults.yaml")))
    try:
        result = publish(resources, report)
        print(result.model_dump_json())
    finally:
        resources.close()


if __name__ == "__main__":
    main()
