"""Strict Markdown headers and exact-source, model-token-aware chunks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tokenizers import Tokenizer

TOKENIZER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
TOKENIZER_SHA256 = "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"
CHUNKING_VERSION = "1.0"
MAX_MODEL_TOKENS = 256
TARGET_TOKENS = 180
OVERLAP_TOKENS = 30
MAX_DOCUMENT_BYTES = 128 * 1024
HEADER_BOUNDARY = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
HEADING = re.compile(r"(?m)^([#]{1,6})[ \t]+(.+?)[ \t]*\r?$", re.MULTILINE)
WORD = re.compile(r"\S+")
DOCUMENT_NAMESPACE = UUID("6756b092-5f31-44a0-91a4-9655ed0941e4")


class DocumentError(ValueError):
    """A document fails the bounded authored-source contract."""


class CuratedServiceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=1, max_length=100)


class DocumentServiceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=100)


class DocumentHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    origin: Literal["authored_demo"]
    document_type: Literal["runbook", "historical_note"]
    published_at: datetime | None
    license: Literal["Apache-2.0", "CC-BY-4.0"]
    attribution: str = Field(min_length=1, max_length=200)
    curated_service_refs: tuple[CuratedServiceReference, ...] = ()
    service_scope: DocumentServiceScope | None = None

    @field_validator("published_at")
    @classmethod
    def utc_publication(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("publication time must include timezone")
        return value.astimezone(UTC)


@dataclass(frozen=True)
class ParsedDocument:
    header: DocumentHeader
    source_text: str
    body_start: int
    source_hash: str
    document_id: UUID


@dataclass(frozen=True)
class DocumentChunk:
    id: UUID
    document_id: UUID
    source_id: str
    start_char: int
    end_char: int
    title: str
    heading: str
    text: str
    token_count: int
    normalized_text_hash: str
    chunking_version: str = CHUNKING_VERSION


def parse_document(raw: bytes) -> ParsedDocument:
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentError("document byte size is outside bounds")
    try:
        source = raw.decode("utf-8")
    except UnicodeError as exc:
        raise DocumentError("document is not valid UTF-8") from exc
    match = HEADER_BOUNDARY.match(source)
    if match is None:
        raise DocumentError("document front matter is missing")
    try:
        data = yaml.safe_load(match.group(1))
        if not isinstance(data, dict):
            raise ValueError("header must be a mapping")
        header = DocumentHeader.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise DocumentError("document header is invalid") from exc
    if not source[match.end() :].strip():
        raise DocumentError("document body is empty")
    source_hash = hashlib.sha256(raw).hexdigest()
    document_id = uuid5(DOCUMENT_NAMESPACE, f"{header.source_id}:{header.version}:{source_hash}")
    return ParsedDocument(header, source, match.end(), source_hash, document_id)


def model_tokenizer() -> Tokenizer:
    path = Path(__file__).resolve().parent / "assets" / "tokenizer.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != TOKENIZER_SHA256:
        raise DocumentError("pinned tokenizer content differs")
    config = json.loads((path.parent / "sentence_bert_config.json").read_text())
    if config.get("max_seq_length") != MAX_MODEL_TOKENS:
        raise DocumentError("encoder context limit differs")
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer


def _sections(document: ParsedDocument) -> list[tuple[str, int, int]]:
    text = document.source_text
    starts: list[tuple[str, int]] = [("", document.body_start)]
    heading_stack: list[tuple[int, str]] = []
    for match in HEADING.finditer(text, document.body_start):
        level = len(match.group(1))
        heading_stack = [item for item in heading_stack if item[0] < level]
        heading_stack.append((level, match.group(2).strip()))
        starts.append((" / ".join(item[1] for item in heading_stack), match.end()))
    sections: list[tuple[str, int, int]] = []
    for index, (heading, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        if index + 1 < len(starts):
            next_heading = HEADING.search(text, start)
            if next_heading is not None and next_heading.start() < end:
                end = next_heading.start()
        if text[start:end].strip():
            sections.append((heading, start, end))
    return sections


def chunk_document(document: ParsedDocument, tokenizer: Tokenizer) -> tuple[DocumentChunk, ...]:
    chunks: list[DocumentChunk] = []
    source = document.source_text
    for heading, section_start, section_end in _sections(document):
        words = list(WORD.finditer(source, section_start, section_end))
        if not words:
            continue
        context = (
            f"{document.header.title}. {heading}. " if heading else f"{document.header.title}. "
        )
        cursor = 0
        while cursor < len(words):
            end_index = cursor
            best_count = 0
            while end_index < len(words):
                excerpt = source[words[cursor].start() : words[end_index].end()]
                count = len(tokenizer.encode(context + excerpt).ids)
                if count > TARGET_TOKENS:
                    break
                best_count = count
                end_index += 1
            if end_index == cursor:
                raise DocumentError("one source word exceeds the chunk token budget")
            start_char = words[cursor].start()
            end_char = words[end_index - 1].end()
            excerpt = source[start_char:end_char]
            if best_count > MAX_MODEL_TOKENS:
                raise DocumentError("chunk exceeds the pinned encoder context")
            chunks.append(
                DocumentChunk(
                    id=uuid5(document.document_id, f"{CHUNKING_VERSION}:{start_char}:{end_char}"),
                    document_id=document.document_id,
                    source_id=document.header.source_id,
                    start_char=start_char,
                    end_char=end_char,
                    title=document.header.title,
                    heading=heading,
                    text=excerpt,
                    token_count=best_count,
                    normalized_text_hash=hashlib.sha256(
                        " ".join(excerpt.casefold().split()).encode()
                    ).hexdigest(),
                )
            )
            if end_index == len(words):
                break
            overlap = 0
            next_cursor = end_index
            while next_cursor > cursor + 1 and overlap < OVERLAP_TOKENS:
                next_cursor -= 1
                overlap = len(
                    tokenizer.encode(
                        source[words[next_cursor].start() : words[end_index - 1].end()],
                        add_special_tokens=False,
                    ).ids
                )
            cursor = next_cursor
    if not chunks:
        raise DocumentError("document has no tokenizable content")
    return tuple(chunks)
