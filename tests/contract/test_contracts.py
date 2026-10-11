import json
import os
from pathlib import Path
from subprocess import run

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from tracecause.config.settings import (
    DefaultConfiguration,
    RuntimeSettings,
    load_defaults,
    load_settings,
)
from tracecause.contracts.canonical import bytes_sha256, semantic_records_sha256, semantic_sha256
from tracecause.contracts.models import (
    EpisodeManifest,
    GraphPath,
    InvestigationRequest,
    InvestigationResult,
    Job,
    Narrative,
    NormalizedSpan,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "configs/defaults.yaml"
EXAMPLES = {
    "manifest": EpisodeManifest,
    "job": Job,
    "narrative": Narrative,
    "investigation-request": InvestigationRequest,
}


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_supplied_examples_validate_in_both_systems(name: str) -> None:
    example = json.loads((ROOT / "examples" / f"{name}.json").read_text())
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
    EXAMPLES[name].model_validate(example)


def test_every_exported_schema_is_valid() -> None:
    for path in (ROOT / "contracts").glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_manifest_baseline_must_be_inside_event_interval() -> None:
    source = json.loads((ROOT / "examples/manifest.json").read_text())
    source["baseline_interval"] = {
        "start": source["event_interval"]["start"],
        "end": source["event_interval"]["end"],
    }
    EpisodeManifest.model_validate(source)
    source["baseline_interval"]["start"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="baseline must be contained"):
        EpisodeManifest.model_validate(source)


def test_schema_exports_are_current() -> None:
    result = run(
        ["uv", "run", "--locked", "python", "scripts/export_contracts.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_openapi_and_frontend_types_include_public_models() -> None:
    openapi = json.loads((ROOT / "contracts/openapi.phase01.json").read_text())
    schemas = openapi["components"]["schemas"]
    frontend = (ROOT / "apps/web/src/lib/api/generated.ts").read_text()
    for path in (ROOT / "contracts").glob("*.schema.json"):
        title = json.loads(path.read_text())["title"]
        assert title in schemas
        assert title in frontend


def _span() -> dict[str, object]:
    return {
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "episode_id": "10000000-0000-4000-8000-000000000001",
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "parent_span_id": None,
        "service_key": "prod/storefront",
        "span_name": "POST /buy",
        "kind": "SERVER",
        "status": "OK",
        "start_ns": "9007199254740993",
        "end_ns": "9007199254740994",
        "available_at": None,
        "safe_attributes": {"http.status_code": 200},
        "source_hash": "a" * 64,
        "source_record_offset": 0,
        "quality_flags": [],
    }


def test_nanosecond_timestamp_round_trip() -> None:
    span = NormalizedSpan.model_validate(_span())
    assert span.start_ns == "9007199254740993"
    serialized = span.model_dump_json()
    assert '"9007199254740993"' in serialized
    assert NormalizedSpan.model_validate_json(serialized).start_ns == span.start_ns


@pytest.mark.parametrize(
    "field,value",
    [
        ("trace_id", "0" * 32),
        ("trace_id", "xyz"),
        ("span_id", "0" * 16),
        ("end_ns", "1"),
        ("fault_target", "payment"),
    ],
)
def test_span_rejects_invalid_or_hidden_fields(field: str, value: str) -> None:
    data = _span()
    data[field] = value
    with pytest.raises(ValidationError):
        NormalizedSpan.model_validate(data)


def test_request_rejects_unknown_enum_oversize_and_gold() -> None:
    data = json.loads((ROOT / "examples/investigation-request.json").read_text())
    for change in (
        {"retrieval_mode": "ALL"},
        {"question": "a" * 2001},
        {"gold_target": "payment"},
    ):
        with pytest.raises(ValidationError):
            InvestigationRequest.model_validate({**data, **change})
        schema = json.loads((ROOT / "contracts/investigation-request.schema.json").read_text())
        assert not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(
            {**data, **change}
        )


def test_interval_order_and_graph_path_continuity() -> None:
    data = json.loads((ROOT / "examples/manifest.json").read_text())
    data["event_interval"]["end"] = data["event_interval"]["start"]
    with pytest.raises(ValidationError):
        EpisodeManifest.model_validate(data)
    path = {
        "path_id": "p1",
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "episode_id": "10000000-0000-4000-8000-000000000001",
        "snapshot_id": "20000000-0000-4000-8000-000000000001",
        "as_of": "2026-01-01T10:02:00Z",
        "service_ids": ["A", "B"],
        "edges": [
            {
                "edge_id": "e",
                "source_service_id": "B",
                "target_service_id": "A",
                "relationship": "CALLS",
                "supporting_evidence_ids": ["e1"],
            }
        ],
    }
    with pytest.raises(ValidationError):
        GraphPath.model_validate(path)


def test_defaults_and_environment_validation(tmp_path: Path) -> None:
    defaults = load_defaults(DEFAULTS)
    assert defaults.application.public_read_only is True
    assert load_settings(DEFAULTS, {}).public_read_only is True
    bad = defaults.model_dump()
    bad["retrieval"]["dimensions"] = 1536
    with pytest.raises(ValidationError):
        DefaultConfiguration.model_validate(bad)
    bad["retrieval"]["dimensions"] = 384
    bad["jobs"]["lease_seconds"] = -1
    with pytest.raises(ValidationError):
        DefaultConfiguration.model_validate(bad)
    with pytest.raises(ValidationError):
        RuntimeSettings(artifact_root=Path("/"))
    with pytest.raises(ValidationError):
        RuntimeSettings(embedding_dimensions=1536)
    with pytest.raises(ValidationError):
        load_settings(DEFAULTS, {"TRACECAUSE_PUBLIC_READ_ONLY": "false"})
    with pytest.raises(ValidationError):
        load_settings(DEFAULTS, {"TRACECAUSE_GENERATION_PROVIDER": "ollama"})
    with pytest.raises(ValidationError):
        RuntimeSettings(neo4j_uri="http://example.com:7687")
    with pytest.raises(ValidationError):
        RuntimeSettings(embedding_revision="main")
    with pytest.raises(ValidationError):
        RuntimeSettings(generation_input_tokens=8000)
    secret = tmp_path / "token"
    secret.write_text("local-operator-token")
    settings = load_settings(
        DEFAULTS,
        {
            "TRACECAUSE_PUBLIC_READ_ONLY": "false",
            "TRACECAUSE_OPERATOR_TOKEN_FILE": str(secret),
        },
    )
    assert settings.operator_token is not None
    assert settings.operator_token.get_secret_value() == "local-operator-token"
    with pytest.raises(ValueError):
        load_settings(
            DEFAULTS,
            {
                "TRACECAUSE_OPERATOR_TOKEN": "direct",
                "TRACECAUSE_OPERATOR_TOKEN_FILE": str(secret),
            },
        )


def test_local_mode_requires_pin_and_opt_in() -> None:
    base = {
        "TRACECAUSE_PUBLIC_READ_ONLY": "false",
        "TRACECAUSE_OPERATOR_TOKEN": "local-only-token",
        "TRACECAUSE_GENERATION_PROVIDER": "ollama",
        "TRACECAUSE_OLLAMA_MODEL_DIGEST": "sha256:" + "a" * 64,
    }
    with pytest.raises(ValidationError):
        load_settings(DEFAULTS, base)
    settings = load_settings(DEFAULTS, {**base, "TRACECAUSE_ENABLE_LOCAL_MODEL": "true"})
    assert settings.generation_provider.value == "ollama"
    with pytest.raises(ValidationError):
        load_settings(
            DEFAULTS,
            {
                **base,
                "TRACECAUSE_ENABLE_LOCAL_MODEL": "true",
                "TRACECAUSE_OLLAMA_URL": "http://example.com:11434",
            },
        )


def test_api_import_fails_fast_for_bad_configuration() -> None:
    env = os.environ.copy()
    env["TRACECAUSE_EMBEDDING_DIMENSIONS"] = "1536"
    result = run(
        ["uv", "run", "--locked", "python", "-c", "import tracecause.api.main"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "MiniLM requires 384" in result.stderr


def test_hashes_keep_source_and_semantics_distinct() -> None:
    first = {"schema_version": "1.0", "id": "a", "value": 1}
    second = {"schema_version": "1.0", "id": "b", "value": 2}
    assert semantic_sha256({"b": 2, "a": 1}) == semantic_sha256({"a": 1, "b": 2})
    assert semantic_records_sha256([first, second]) == semantic_records_sha256([second, first])
    assert bytes_sha256(b"a\nb\n") != bytes_sha256(b"b\na\n")


def _result() -> dict[str, object]:
    workspace = "00000000-0000-4000-8000-000000000001"
    episode = "10000000-0000-4000-8000-000000000001"
    snapshot = "20000000-0000-4000-8000-000000000001"
    return {
        "schema_version": "1.0",
        "investigation_id": "40000000-0000-4000-8000-000000000001",
        "workspace_id": workspace,
        "episode_id": episode,
        "snapshot_id": snapshot,
        "as_of": "2026-01-01T10:02:00Z",
        "temporal_mode": "event_time_replay",
        "retrieval_mode": "GRAPH",
        "ranking_mode": "heuristic",
        "generation_origin": "template",
        "is_replayed": False,
        "fallback_reason": None,
        "narrative": {
            "status": "insufficient_evidence",
            "summary": {"text": "Only one observation is available.", "evidence_ids": ["e1"]},
            "hypotheses": [],
            "claims": [],
            "limitations": ["No baseline is present."],
        },
        "evidence": [
            {
                "evidence_id": "e1",
                "workspace_id": workspace,
                "episode_id": episode,
                "snapshot_id": snapshot,
                "kind": "observation",
                "text": "One completed request.",
                "interval": {"start": "2026-01-01T10:00:00Z", "end": "2026-01-01T10:01:00Z"},
                "published_at": None,
                "available_at": None,
                "locator": {
                    "source_hash": "a" * 64,
                    "record_offset": 0,
                    "start_char": None,
                    "end_char": None,
                    "trace_id": "1" * 32,
                    "span_ids": ["2" * 16],
                },
                "metrics": [],
                "quality_flags": [],
            }
        ],
        "paths": [],
        "ranked_scores": [],
        "provenance": {
            "input_manifest_hash": "a" * 64,
            "config_hash": "b" * 64,
            "prompt_hash": None,
            "model_digest": None,
            "embedding_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            "feature_schema_version": "1.0",
        },
        "timings_ms": {},
        "usage": {"prompt_tokens": None, "output_tokens": None},
        "warnings": [],
    }


def test_result_scope_cutoff_and_citation_membership() -> None:
    valid = _result()
    InvestigationResult.model_validate(valid)
    schema = json.loads((ROOT / "contracts/investigation-result.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(valid)
    foreign = _result()
    foreign["evidence"][0]["snapshot_id"] = "20000000-0000-4000-8000-000000000002"
    with pytest.raises(ValidationError):
        InvestigationResult.model_validate(foreign)
    future = _result()
    future["evidence"][0]["interval"]["end"] = "2026-01-01T10:03:00Z"
    with pytest.raises(ValidationError):
        InvestigationResult.model_validate(future)
    unknown = _result()
    unknown["narrative"]["summary"]["evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError):
        InvestigationResult.model_validate(unknown)
