"""Real subprocess/HTTP/queue capture checks; no model or database substitutes."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.capture.minishop import quality
from scripts.capture.operational_lab import APPLICATIONS, Application

from tracecause.operational.models import OperationalBundle


@pytest.mark.parametrize("index", range(3))
def test_real_application_records_calls_release_config_and_clean_recovery(
    tmp_path: Path, index: int
) -> None:
    spec = APPLICATIONS[index]
    app = Application(spec, tmp_path / "raw", tmp_path / "artifacts")
    try:
        app.start()
        baseline = app.workload(0.4, rate=10)
        target = spec["target"]
        deployment = app.deploy(target, "1.1.0")
        changed = app.workload(0.4, rate=10)
        app.deploy(target, "1.0.0")
        config = app.configure(target, 1)
        limited = app.workload(0.4, rate=10)
        app.configure(target, 64)
        recovery = app.workload(0.4, rate=10)
    finally:
        app.close()
    assert baseline["outcomes"].keys() == recovery["outcomes"].keys() == {"200"}
    assert changed["outcomes"]["503"] > 0
    assert limited["outcomes"].keys() == {"503"}
    parsed = OperationalBundle.model_validate(
        {
            "schema_version": "1.0",
            "application_id": spec["id"],
            "application_name": spec["name"],
            "topology": spec["topology"],
            "events": app.events,
        }
    )
    by_id = {str(event.id): event for event in parsed.events}
    assert by_id[deployment].kind == "deployment"
    assert by_id[config].kind == "configuration"
    source = b"".join(path.read_bytes() for path in sorted((tmp_path / "raw").glob("*.jsonl")))
    stats = quality(source, uuid4())
    assert stats["accepted_spans"] > 0
    assert stats["missing_parents"] == stats["rejected_spans"] == 0
    assert set(stats["service_counts"]) == set(spec["roles"])
    assert stats["direct_client_server_pairs"] > 0
    for event in parsed.events:
        assert "target" not in event.details
        assert "intervention" not in event.details
    if spec["topology"] == "asynchronous-queue":
        spans = [
            span
            for line in source.splitlines()
            for resource in json.loads(line)["resourceSpans"]
            for scope in resource["scopeSpans"]
            for span in scope["spans"]
        ]
        producers = {(span["traceId"], span["spanId"]) for span in spans if span["kind"] == 4}
        consumers = [span for span in spans if span["kind"] == 5]
        assert consumers
        assert all((span["traceId"], span["parentSpanId"]) in producers for span in consumers)
