"""Official OTLP conformance and adversarial normalization cases."""

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tracecause.contracts.models import NormalizedSpan
from tracecause.ingestion.identity import AliasRegistry, AliasRule
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import (
    InputEncoding,
    OtlpJsonParser,
    ParseLimits,
    UnsupportedEncoding,
)

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = UUID("00000000-0000-4000-8000-000000000001")
EPISODE = UUID("10000000-0000-4000-8000-000000000001")
OFFICIAL = ROOT / "data/fixtures/official/trace.json"


def _official() -> dict[str, object]:
    return json.loads(OFFICIAL.read_text())


def _span(envelope: dict[str, object]) -> dict[str, object]:
    return envelope["resourceSpans"][0]["scopeSpans"][0]["spans"][0]  # type: ignore[index]


def _run(
    raw: bytes,
    *,
    encoding: InputEncoding = InputEncoding.OTLP_JSON_V1,
    aliases: AliasRegistry | None = None,
    limits: ParseLimits | None = None,
):
    return normalize_otlp(
        io.BytesIO(raw),
        encoding=encoding,
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        expected_source_hash=hashlib.sha256(raw).hexdigest(),
        source_name="trace.json",
        aliases=aliases,
        limits=limits,
    )


def _encode(envelope: dict[str, object]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def test_official_otlp_json_fixture_and_nanoseconds() -> None:
    raw = OFFICIAL.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "f8f2870852b247f734a53ca7f022d4d942bd29732df54440494948af181bd373"
    )
    batch = _run(raw)
    assert (batch.accepted_count, batch.rejected_count, batch.fatal) == (1, 0, False)
    span = batch.spans[0]
    assert isinstance(span, NormalizedSpan)
    assert span.trace_id == "5b8efff798038103d269b633813fc60c"
    assert span.span_id == "eee19b7ec3c1b174"
    assert span.parent_span_id == "eee19b7ec3c1b173"
    assert span.start_ns == "1544712660000000000"
    assert span.kind == "SERVER" and span.status == "UNSET"
    assert span.service_name == "my.service"
    assert "missing_parent" in span.quality_flags


def test_allowlist_redacts_nested_secrets_and_preserves_input_hash() -> None:
    envelope = _official()
    span = _span(envelope)
    secret = "Bearer abcDEF123456789012345678901234"
    span["name"] = "GET /users/jane@example.com?access_token=" + secret
    span["attributes"] = [
        {"key": "http.request.header.authorization", "value": {"stringValue": secret}},
        {"key": "http.request.body", "value": {"stringValue": secret}},
        {"key": "exception.message", "value": {"stringValue": secret}},
        {"key": "http.response.status_code", "value": {"intValue": "503"}},
        {"key": "exception.type", "value": {"stringValue": "TimeoutError"}},
        {
            "key": "custom.nested",
            "value": {
                "kvlistValue": {"values": [{"key": "secret", "value": {"stringValue": secret}}]}
            },
        },
    ]
    raw = _encode(envelope)
    batch = _run(raw)
    output = batch.spans[0].model_dump_json()
    assert secret not in output and "jane@example.com" not in output
    assert "access_token" not in output
    assert batch.spans[0].span_name == "<redacted-operation>"
    assert batch.spans[0].safe_attributes == {
        "http.response.status_code": 503,
        "exception.type": "TimeoutError",
    }
    assert batch.source_hash == hashlib.sha256(raw).hexdigest()
    assert batch.redacted_hash != batch.source_hash
    assert raw == _encode(envelope)


def test_dynamic_operation_and_sensitive_service_identity_are_not_exposed() -> None:
    envelope = _official()
    _span(envelope)["name"] = "GET /users/123456"
    attrs = envelope["resourceSpans"][0]["resource"]["attributes"]  # type: ignore[index]
    attrs[0]["value"]["stringValue"] = "api_token_abcDEF123456789012345678901234"
    batch = _run(_encode(envelope))
    assert batch.spans[0].span_name == "<redacted-operation>"
    assert batch.spans[0].service_key is None
    assert batch.spans[0].service_name is None
    _span(envelope)["name"] = "GET /users/jane"
    assert _run(_encode(envelope)).spans[0].span_name == "<redacted-operation>"


def test_excessive_nested_attribute_is_rejected() -> None:
    envelope = _official()
    nested: dict[str, object] = {"stringValue": "safe"}
    for _ in range(20):
        nested = {"next": nested}
    _span(envelope)["attributes"] = [{"key": "unknown", "value": nested}]
    batch = _run(_encode(envelope))
    assert batch.accepted_count == 0
    assert batch.diagnostics[0].code == "excessive_nesting"


def test_alias_is_versioned_and_cannot_cross_namespace_or_environment() -> None:
    rules = (AliasRule("shop", "prod", "pay", "payment", "reviewed-runbook-v1"),)
    registry = AliasRegistry("2026-10", rules)
    first = registry.resolve(WORKSPACE, "shop", "prod", "pay")
    second = registry.resolve(WORKSPACE, "shop", "staging", "pay")
    third = registry.resolve(WORKSPACE, "other", "prod", "pay")
    canonical = registry.resolve(WORKSPACE, "shop", "prod", "payment")
    assert first.key == canonical.key
    assert first.alias_source == "reviewed-runbook-v1"
    assert first.alias_version == "2026-10"
    assert first.key != second.key != third.key
    assert registry.resolve(WORKSPACE, "shop", "prod", None).unresolved


def test_duplicate_and_conflict_quarantine_are_deterministic() -> None:
    envelope = _official()
    span = _span(envelope)
    spans = envelope["resourceSpans"][0]["scopeSpans"][0]["spans"]  # type: ignore[index]
    spans.append(deepcopy(span))
    duplicate = _run(_encode(envelope))
    assert (duplicate.accepted_count, duplicate.duplicate_count, duplicate.conflict_count) == (
        1,
        1,
        0,
    )
    spans[-1]["endTimeUnixNano"] = "1544712662000000000"
    conflict = _run(_encode(envelope))
    assert (conflict.accepted_count, conflict.rejected_count, conflict.conflict_count) == (0, 2, 1)
    assert conflict.diagnostics[0].code == "conflicting_duplicate"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda s: s.update(traceId="z" * 32), "invalid_trace_id"),
        (lambda s: s.update(spanId="0" * 16), "invalid_span_id"),
        (lambda s: s.update(endTimeUnixNano="1"), "negative_duration"),
        (lambda s: s.update(startTimeUnixNano="1.25"), "invalid_start_ns"),
        (lambda s: s.update(status={"code": 3}), "invalid_status"),
        (lambda s: s.update(kind="SPAN_KIND_SERVER"), "invalid_kind"),
        (lambda s: s.update(traceId="W47o/q=="), "invalid_trace_id"),
    ],
)
def test_invalid_spans_are_rejected_without_raw_diagnostics(change, code: str) -> None:
    envelope = _official()
    change(_span(envelope))
    batch = _run(_encode(envelope))
    assert batch.accepted_count == 0 and batch.rejected_count == 1
    assert batch.diagnostics[0].code == code
    assert "W47o" not in str(batch.diagnostics)


def test_missing_resource_identity_is_unresolved() -> None:
    envelope = _official()
    envelope["resourceSpans"][0].pop("resource")  # type: ignore[index]
    batch = _run(_encode(envelope))
    assert batch.accepted_count == 1
    assert batch.spans[0].service_key is None
    assert set(batch.spans[0].quality_flags) == {
        "missing_resource",
        "unresolved_service_identity",
        "missing_namespace",
        "missing_environment",
        "missing_parent",
    }


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'{"resourceSpans": [', "malformed_json"),
        (b'{"resourceSpans":[]}', "empty_batch"),
        (b'{"schemaVersion":"2.0","resourceSpans":[]}', "unsupported_schema_version"),
        (b'{"resourceLogs":[]}', "unsupported_signal"),
        (b"\x1f\x8bcompressed", "unsupported_compression"),
        (b"\xff\xfe", "unsupported_encoding"),
        (b"", "empty_file"),
    ],
)
def test_invalid_export_requests(raw: bytes, code: str) -> None:
    batch = _run(raw, encoding=InputEncoding.OTLP_JSONL_V1)
    assert batch.accepted_count == 0
    assert batch.diagnostics[0].code == code


def test_out_of_order_ndjson_and_byte_locators() -> None:
    envelope = _official()
    first = _span(envelope)
    child = deepcopy(first)
    child["spanId"] = "aaaaaaaaaaaaaaaa"
    child["parentSpanId"] = first["spanId"]
    parent = deepcopy(envelope)
    child_envelope = deepcopy(envelope)
    child_envelope["resourceSpans"][0]["scopeSpans"][0]["spans"] = [child]  # type: ignore[index]
    first_line = _encode(child_envelope) + b"\n"
    raw = first_line + _encode(parent) + b"\n"
    batch = _run(raw, encoding=InputEncoding.OTLP_JSONL_V1)
    assert batch.accepted_count == 2 and not batch.fatal
    assert "missing_parent" not in batch.spans[0].quality_flags
    assert batch.spans[1].source_record_offset == len(first_line)
    assert batch.spans[1].source_line_number == 2
    assert batch.spans[1].source_span_index == 0


def test_limits_stop_reading_without_loading_entire_stream() -> None:
    class ReadlineOnly(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise AssertionError("NDJSON parser must stream by line")

    raw = _encode(_official()) + b"\n"
    parser = OtlpJsonParser(ReadlineOnly(raw), InputEncoding.OTLP_JSONL_V1)
    assert len(list(parser.records())) == 1
    assert parser.bytes_read == len(raw)
    oversize = _run(
        raw, encoding=InputEncoding.OTLP_JSONL_V1, limits=ParseLimits(max_line_bytes=10)
    )
    assert oversize.fatal and oversize.diagnostics[0].code == "line_too_large"


def test_collector_encoding_is_explicitly_unsupported_until_conformance() -> None:
    with pytest.raises(UnsupportedEncoding, match="pinned capture"):
        OtlpJsonParser(io.BytesIO(b"{}"), InputEncoding.COLLECTOR_FILE_JSON_V1)


@settings(max_examples=30)
@given(
    st.one_of(
        st.integers(min_value=-(2**63), max_value=-1),
        st.integers(min_value=2**63, max_value=2**64),
    )
)
def test_invalid_nanosecond_ranges_never_create_spans(timestamp: int) -> None:
    envelope = _official()
    _span(envelope)["startTimeUnixNano"] = str(timestamp)
    batch = _run(_encode(envelope))
    assert batch.accepted_count == 0
    assert batch.diagnostics[0].code == "invalid_start_ns"


def test_decimal_nanoseconds_are_canonicalized_without_float_rounding() -> None:
    envelope = _official()
    _span(envelope)["startTimeUnixNano"] = "01544712660000000000"
    batch = _run(_encode(envelope))
    assert batch.spans[0].start_ns == "1544712660000000000"
