"""Versioned canonical serialization separates raw bytes from semantic facts."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel


def canonical_json(value: BaseModel | Mapping[str, Any]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def bytes_sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def semantic_sha256(value: BaseModel | Mapping[str, Any]) -> str:
    return bytes_sha256(canonical_json(value))


def semantic_records_sha256(records: Iterable[BaseModel | Mapping[str, Any]]) -> str:
    canonical_records = sorted(canonical_json(record) for record in records)
    return bytes_sha256(b"[" + b",".join(canonical_records) + b"]")
