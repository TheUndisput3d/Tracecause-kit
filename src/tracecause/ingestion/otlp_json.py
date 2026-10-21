"""Bounded streaming OTLP/JSON export-request parser with exact source locators."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, BinaryIO

from tracecause.ingestion.redaction import bounded_depth


class InputEncoding(StrEnum):
    OTLP_JSONL_V1 = "otlp_jsonl_v1"
    OTLP_JSON_V1 = "otlp_json_v1"
    COLLECTOR_FILE_JSON_V1 = "collector_file_json_v1"


@dataclass(frozen=True)
class ParseLimits:
    max_line_bytes: int = 4 * 1024 * 1024
    max_file_bytes: int = 25 * 1024 * 1024
    max_depth: int = 16
    max_items: int = 128


@dataclass(frozen=True)
class SourceLocator:
    byte_offset: int
    line_number: int
    resource_index: int
    scope_index: int
    span_index: int


@dataclass(frozen=True)
class RawSpan:
    resource: dict[str, Any]
    scope: dict[str, Any]
    span: dict[str, Any]
    locator: SourceLocator


@dataclass(frozen=True)
class ParseDiagnostic:
    code: str
    byte_offset: int
    line_number: int
    fatal: bool = False


class UnsupportedEncoding(ValueError):
    """An exporter encoding has not been verified for this adapter."""


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


class OtlpJsonParser:
    def __init__(
        self, reader: BinaryIO, encoding: InputEncoding, limits: ParseLimits | None = None
    ):
        if encoding is InputEncoding.COLLECTOR_FILE_JSON_V1:
            raise UnsupportedEncoding("Collector file encoding awaits pinned capture conformance")
        self.reader = reader
        self.encoding = encoding
        self.limits = limits or ParseLimits()
        self._hash = hashlib.sha256()
        self.bytes_read = 0

    @property
    def source_hash(self) -> str:
        return self._hash.hexdigest()

    def _traverse(
        self, raw: bytes, byte_offset: int, line_number: int
    ) -> Iterator[RawSpan | ParseDiagnostic]:
        if raw.startswith(b"\x1f\x8b"):
            yield ParseDiagnostic("unsupported_compression", byte_offset, line_number, True)
            return
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            yield ParseDiagnostic("unsupported_encoding", byte_offset, line_number)
            return
        try:
            envelope = json.loads(decoded, parse_constant=_reject_constant)
        except ValueError:
            yield ParseDiagnostic("malformed_json", byte_offset, line_number)
            return
        if not isinstance(envelope, dict):
            yield ParseDiagnostic("invalid_export_request", byte_offset, line_number)
            return
        if not bounded_depth(
            envelope, max_depth=self.limits.max_depth, max_items=self.limits.max_items
        ):
            yield ParseDiagnostic("excessive_nesting", byte_offset, line_number)
            return
        if envelope.get("schemaVersion", "1.0") != "1.0":
            yield ParseDiagnostic("unsupported_schema_version", byte_offset, line_number)
            return
        resources = envelope.get("resourceSpans")
        if not isinstance(resources, list):
            code = (
                "unsupported_signal"
                if "resourceLogs" in envelope or "resourceMetrics" in envelope
                else "missing_resource_spans"
            )
            yield ParseDiagnostic(code, byte_offset, line_number)
            return
        if not resources:
            yield ParseDiagnostic("empty_batch", byte_offset, line_number)
            return
        for resource_index, item in enumerate(resources):
            if not isinstance(item, dict) or not isinstance(item.get("scopeSpans"), list):
                yield ParseDiagnostic("invalid_resource_spans", byte_offset, line_number)
                continue
            resource = item.get("resource", {})
            if not isinstance(resource, dict):
                yield ParseDiagnostic("invalid_resource", byte_offset, line_number)
                continue
            for scope_index, scope_item in enumerate(item["scopeSpans"]):
                if not isinstance(scope_item, dict) or not isinstance(
                    scope_item.get("spans"), list
                ):
                    yield ParseDiagnostic("invalid_scope_spans", byte_offset, line_number)
                    continue
                scope = scope_item.get("scope", {})
                if not isinstance(scope, dict):
                    yield ParseDiagnostic("invalid_scope", byte_offset, line_number)
                    continue
                for span_index, span in enumerate(scope_item["spans"]):
                    if not isinstance(span, dict):
                        yield ParseDiagnostic("invalid_span", byte_offset, line_number)
                        continue
                    yield RawSpan(
                        resource,
                        scope,
                        span,
                        SourceLocator(
                            byte_offset, line_number, resource_index, scope_index, span_index
                        ),
                    )

    def records(self) -> Iterator[RawSpan | ParseDiagnostic]:
        if self.encoding is InputEncoding.OTLP_JSON_V1:
            raw = self.reader.read(self.limits.max_file_bytes + 1)
            self.bytes_read = len(raw)
            if len(raw) > self.limits.max_file_bytes:
                yield ParseDiagnostic("file_too_large", 0, 1, True)
                return
            self._hash.update(raw)
            if not raw.strip():
                yield ParseDiagnostic("empty_file", 0, 1)
                return
            yield from self._traverse(raw, 0, 1)
            return
        line_number = 0
        while True:
            byte_offset = self.bytes_read
            raw = self.reader.readline(self.limits.max_line_bytes + 1)
            if not raw:
                break
            line_number += 1
            self.bytes_read += len(raw)
            if self.bytes_read > self.limits.max_file_bytes:
                yield ParseDiagnostic("file_too_large", byte_offset, line_number, True)
                return
            if len(raw) > self.limits.max_line_bytes:
                yield ParseDiagnostic("line_too_large", byte_offset, line_number, True)
                return
            self._hash.update(raw)
            if not raw.strip():
                yield ParseDiagnostic("empty_line", byte_offset, line_number)
                continue
            yield from self._traverse(raw, byte_offset, line_number)
        if line_number == 0:
            yield ParseDiagnostic("empty_file", 0, 1)
