"""Export one Pydantic source of truth to JSON Schema and OpenAPI components."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from tracecause.api.main import app
from tracecause.contracts import models
from tracecause.operational.api_models import OperationsResponse
from tracecause.operational.handoffs import HandoffEvidence
from tracecause.operational.models import OperationalBundle, OperationalEvidence

ROOT = Path(__file__).resolve().parents[1]
PUBLIC: dict[str, type[BaseModel]] = {
    "handoff-evidence": HandoffEvidence,
    "operational-bundle": OperationalBundle,
    "operations-response": OperationsResponse,
    "operational-evidence": OperationalEvidence,
    "source-metadata": models.SourceMetadata,
    "manifest": models.EpisodeManifest,
    "import-request": models.ImportRequest,
    "import-report": models.ImportReport,
    "service-identity": models.ServiceIdentity,
    "normalized-span": models.NormalizedSpan,
    "observation": models.AggregateObservation,
    "document": models.Document,
    "chunk": models.Chunk,
    "assertion": models.Assertion,
    "snapshot": models.Snapshot,
    "job": models.Job,
    "investigation-request": models.InvestigationRequest,
    "investigation-result": models.InvestigationResult,
    "evidence": models.Evidence,
    "graph-path": models.GraphPath,
    "graph-response": models.GraphResponse,
    "knowledge-response": models.KnowledgeResponse,
    "knowledge-checks-response": models.KnowledgeChecksResponse,
    "narrative": models.Narrative,
    "retrieval-request": models.RetrievalRequest,
    "retrieval-result": models.RetrievalResult,
    "evidence-bundle": models.EvidenceBundle,
    "evaluation-manifest": models.EvaluationManifest,
    "evaluation-report": models.EvaluationReport,
    "error-envelope": models.ErrorEnvelope,
    "episode-view": models.EpisodeView,
    "episode-page": models.EpisodePage,
    "job-view": models.JobView,
    "investigation-submission": models.InvestigationSubmission,
    "import-submission-view": models.ImportSubmissionView,
    "investigation-view": models.InvestigationView,
    "investigation-page": models.InvestigationPage,
    "series-point": models.SeriesPoint,
    "series-response": models.SeriesResponse,
    "evaluation-page": models.EvaluationPage,
    "api-capabilities": models.ApiCapabilities,
    "health-status": models.HealthStatus,
    "auth-status": models.AuthStatus,
}


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _output_files() -> dict[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    for name, model in PUBLIC.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        outputs[ROOT / "contracts" / f"{name}.schema.json"] = _encoded(schema)
    outputs[ROOT / "apps/web/src/lib/api/schemas.json"] = _encoded(
        {model.__name__: model.model_json_schema() for model in PUBLIC.values()}
    )
    _, union = models_json_schema(
        [(model, "validation") for model in PUBLIC.values()],
        ref_template="#/components/schemas/{model}",
    )
    openapi = app.openapi().copy()
    components = openapi.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(union["$defs"])
    outputs[ROOT / "contracts/openapi.phase01.json"] = _encoded(openapi)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed: list[str] = []
    for path, content in _output_files().items():
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                changed.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if changed:
        print("Schema exports out of date: " + ", ".join(changed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
