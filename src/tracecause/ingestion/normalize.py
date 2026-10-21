"""Pure OTLP span normalization, redaction and deterministic deduplication."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal, cast
from uuid import UUID

from tracecause.contracts.models import ImportDiagnostic, NormalizedSpan
from tracecause.ingestion.identity import AliasRegistry
from tracecause.ingestion.otlp_json import (
    InputEncoding,
    OtlpJsonParser,
    ParseDiagnostic,
    ParseLimits,
    RawSpan,
)
from tracecause.ingestion.redaction import attribute_map, safe_operation, safe_span_attributes

TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
SPAN_ID = re.compile(r"^[0-9a-fA-F]{16}$")
DECIMAL_NS = re.compile(r"^[0-9]{1,20}$")
KIND = {
    0: "UNSPECIFIED",
    1: "INTERNAL",
    2: "SERVER",
    3: "CLIENT",
    4: "PRODUCER",
    5: "CONSUMER",
}
STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


class InvalidSpan(ValueError):
    """A safe code for a rejected span; the raw value is never included."""


@dataclass(frozen=True)
class NormalizedBatch:
    spans: tuple[NormalizedSpan, ...]
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    conflict_count: int
    diagnostics: tuple[ImportDiagnostic, ...]
    source_hash: str
    redacted_hash: str
    fatal: bool


def _hex_id(
    value: Any, pattern: re.Pattern[str], name: str, *, optional: bool = False
) -> str | None:
    if optional and (value is None or value == ""):
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value) or set(value) == {"0"}:
        raise InvalidSpan(f"invalid_{name}")
    return value.lower()


def _nanoseconds(value: Any, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InvalidSpan(f"invalid_{name}")
    string = str(value)
    if not DECIMAL_NS.fullmatch(string) or int(string) > 2**63 - 1:
        raise InvalidSpan(f"invalid_{name}")
    return str(int(string))


def _enum(value: Any, allowed: dict[int, str], name: str) -> str:
    if type(value) is not int or value not in allowed:
        raise InvalidSpan(f"invalid_{name}")
    return allowed[value]


def _string_attr(attributes: dict[str, str | int | float | bool], key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) else None


def normalize_span(
    raw: RawSpan,
    workspace_id: UUID,
    episode_id: UUID,
    source_hash: str,
    aliases: AliasRegistry,
) -> NormalizedSpan:
    span = raw.span
    trace_id = _hex_id(span.get("traceId"), TRACE_ID, "trace_id")
    span_id = _hex_id(span.get("spanId"), SPAN_ID, "span_id")
    parent_id = _hex_id(span.get("parentSpanId"), SPAN_ID, "parent_id", optional=True)
    assert trace_id is not None and span_id is not None
    start_ns = _nanoseconds(span.get("startTimeUnixNano"), "start_ns")
    end_ns = _nanoseconds(span.get("endTimeUnixNano"), "end_ns")
    if int(end_ns) < int(start_ns):
        raise InvalidSpan("negative_duration")
    status_value = span.get("status", {})
    if not isinstance(status_value, dict):
        raise InvalidSpan("invalid_status")
    kind = cast(
        Literal["INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER", "UNSPECIFIED"],
        _enum(span.get("kind", 0), KIND, "kind"),
    )
    status = cast(
        Literal["UNSET", "OK", "ERROR"], _enum(status_value.get("code", 0), STATUS, "status")
    )
    try:
        resource_attrs = attribute_map(raw.resource.get("attributes", []))
        safe_attrs = safe_span_attributes(span.get("attributes", []))
    except ValueError as exc:
        raise InvalidSpan("invalid_attributes") from exc
    resolution = aliases.resolve(
        workspace_id,
        _string_attr(resource_attrs, "service.namespace"),
        _string_attr(resource_attrs, "deployment.environment.name"),
        _string_attr(resource_attrs, "service.name"),
    )
    flags: list[str] = []
    if resolution.unresolved:
        flags.append("unresolved_service_identity")
    if resolution.namespace is None:
        flags.append("missing_namespace")
    if resolution.environment is None:
        flags.append("missing_environment")
    if raw.resource == {}:
        flags.append("missing_resource")
    return NormalizedSpan(
        workspace_id=workspace_id,
        episode_id=episode_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_id,
        service_key=resolution.key,
        service_namespace=resolution.namespace,
        service_environment=resolution.environment,
        service_name=resolution.name,
        alias_version=resolution.alias_version,
        alias_source_reference=resolution.alias_source,
        span_name=safe_operation(span.get("name")),
        kind=kind,
        status=status,
        start_ns=start_ns,
        end_ns=end_ns,
        available_at=None,
        safe_attributes=safe_attrs,
        source_hash=source_hash,
        source_record_offset=raw.locator.byte_offset,
        source_line_number=raw.locator.line_number,
        source_resource_index=raw.locator.resource_index,
        source_scope_index=raw.locator.scope_index,
        source_span_index=raw.locator.span_index,
        quality_flags=flags,
    )


def normalized_span_semantic_hash(span: NormalizedSpan) -> str:
    payload = span.model_dump(
        mode="json",
        exclude={
            "source_hash",
            "source_record_offset",
            "source_line_number",
            "source_resource_index",
            "source_scope_index",
            "source_span_index",
            "quality_flags",
        },
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def serialized_spans(spans: tuple[NormalizedSpan, ...]) -> bytes:
    return b"".join(serialized_span(span) for span in spans)


def serialized_span(span: NormalizedSpan) -> bytes:
    return (
        json.dumps(span.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def normalize_otlp(
    reader: BinaryIO,
    *,
    encoding: InputEncoding,
    workspace_id: UUID,
    episode_id: UUID,
    expected_source_hash: str,
    source_name: str,
    aliases: AliasRegistry | None = None,
    limits: ParseLimits | None = None,
) -> NormalizedBatch:
    parser = OtlpJsonParser(reader, encoding, limits)
    registry = aliases or AliasRegistry()
    accepted: dict[tuple[str, str], tuple[NormalizedSpan, str]] = {}
    quarantined: set[tuple[str, str]] = set()
    rejected = duplicates = conflicts = 0
    diagnostics: list[ImportDiagnostic] = []
    fatal = False

    def diagnose(code: str, offset: int) -> None:
        if len(diagnostics) < 20:
            diagnostics.append(
                ImportDiagnostic(
                    code=code,
                    source_name=source_name,
                    record_offset=offset,
                    safe_message=code.replace("_", " "),
                )
            )

    for record in parser.records():
        if isinstance(record, ParseDiagnostic):
            rejected += 1
            diagnose(record.code, record.byte_offset)
            if record.fatal:
                fatal = True
                break
            continue
        try:
            span = normalize_span(record, workspace_id, episode_id, expected_source_hash, registry)
        except InvalidSpan as exc:
            rejected += 1
            diagnose(str(exc), record.locator.byte_offset)
            continue
        key = (span.trace_id, span.span_id)
        if key in quarantined:
            rejected += 1
            continue
        signature = normalized_span_semantic_hash(span)
        previous = accepted.get(key)
        if previous is None:
            accepted[key] = (span, signature)
        elif previous[1] == signature:
            duplicates += 1
        else:
            accepted.pop(key)
            quarantined.add(key)
            conflicts += 1
            rejected += 2
            diagnose("conflicting_duplicate", record.locator.byte_offset)
    source_hash = parser.source_hash
    if not fatal and source_hash != expected_source_hash:
        fatal = True
        diagnose("source_hash_mismatch", 0)
    all_ids = set(accepted)
    spans = tuple(
        span.model_copy(
            update={
                "quality_flags": span.quality_flags + ["missing_parent"]
                if span.parent_span_id and (span.trace_id, span.parent_span_id) not in all_ids
                else span.quality_flags
            }
        )
        for span, _ in accepted.values()
    )
    if fatal:
        spans = ()
    output_hash = hashlib.sha256()
    for span in spans:
        output_hash.update(serialized_span(span))
    redacted_hash = output_hash.hexdigest()
    return NormalizedBatch(
        spans,
        len(spans),
        rejected,
        duplicates,
        conflicts,
        tuple(diagnostics),
        source_hash,
        redacted_hash,
        fatal,
    )
