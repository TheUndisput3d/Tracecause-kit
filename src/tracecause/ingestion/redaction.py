"""Allowlist-first handling of untrusted OTLP attributes and operation names."""

import math
import re
from typing import Any

REDACTION_VERSION = "1.0"
SAFE_CATEGORY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
SAFE_OPERATION = re.compile(r"^[A-Za-z0-9 /_.:'-]{1,120}$")
DYNAMIC_SEGMENT = re.compile(r"/(?:[0-9]{3,}|[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,})(?:/|$)")
SENSITIVE = re.compile(
    r"(?i)(authorization|bearer|cookie|password|secret|token|api[_-]?key|"
    r"https?://|@|\?|\b[0-9]{3}[- ]?[0-9]{2}[- ]?[0-9]{4}\b|"
    r"\b[0-9]{9,}\b|[0-9a-f]{24,}|[A-Za-z0-9+/]{32,}={0,2})"
)
SAFE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
SAFE_HTTP_OPERATIONS = {
    "POST /buy",
    "POST /checkout",
    "POST /authorize",
    "GET /quote",
    "GET /availability",
    "POST /assess",
    "GET /stock",
    "GET /health",
}
STATUS_KEYS = {"http.response.status_code", "http.status_code", "rpc.grpc.status_code"}
CATEGORY_KEYS = {"error.type", "exception.type"}


def bounded_depth(value: Any, *, max_depth: int = 8, max_items: int = 128) -> bool:
    """Reject deeply nested or very wide input before extracting any attribute."""
    stack = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if depth > max_depth or visited > max_items * 20:
            return False
        if isinstance(item, dict):
            if len(item) > max_items:
                return False
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > max_items:
                return False
            stack.extend((child, depth + 1) for child in item)
    return True


def _scalar(value: Any) -> str | int | float | bool | None:
    if not isinstance(value, dict) or len(value) != 1:
        return None
    key, raw = next(iter(value.items()))
    if key == "stringValue" and isinstance(raw, str):
        return raw
    if key == "boolValue" and isinstance(raw, bool):
        return raw
    if key == "intValue" and not isinstance(raw, bool):
        if isinstance(raw, int):
            return raw if -(2**63) <= raw <= 2**63 - 1 else None
        if isinstance(raw, str) and re.fullmatch(r"-?[0-9]{1,20}", raw):
            parsed = int(raw)
            return parsed if -(2**63) <= parsed <= 2**63 - 1 else None
    if key == "doubleValue" and isinstance(raw, (float, int)) and not isinstance(raw, bool):
        try:
            value = float(raw)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
    return None


def attribute_map(attributes: Any) -> dict[str, str | int | float | bool]:
    if not isinstance(attributes, list) or len(attributes) > 128:
        raise ValueError("invalid or excessive attributes")
    result: dict[str, str | int | float | bool] = {}
    for item in attributes:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ValueError("invalid attribute entry")
        key = item["key"]
        if key in result:
            continue
        value = _scalar(item.get("value"))
        if value is not None:
            result[key] = value
    return result


def safe_span_attributes(attributes: Any) -> dict[str, str | int | float | bool]:
    raw = attribute_map(attributes)
    safe: dict[str, str | int | float | bool] = {}
    for key, value in raw.items():
        if key in STATUS_KEYS and type(value) is int and 0 <= value <= 999:
            safe[key] = value
        elif key == "http.request.method" and isinstance(value, str) and value in SAFE_METHODS:
            safe[key] = value
        elif (
            key in CATEGORY_KEYS
            and isinstance(value, str)
            and SAFE_CATEGORY.fullmatch(value)
            and not SENSITIVE.search(value)
        ):
            safe[key] = value
    return safe


def safe_operation(name: Any) -> str:
    if (
        not isinstance(name, str)
        or not SAFE_OPERATION.fullmatch(name)
        or SENSITIVE.search(name)
        or DYNAMIC_SEGMENT.search(name)
        or (re.match(r"^[A-Z]+ /", name) and name not in SAFE_HTTP_OPERATIONS)
    ):
        return "<redacted-operation>"
    return name


def safe_display_name(name: str) -> str:
    if not name or len(name) > 120 or SENSITIVE.search(name):
        return "<redacted-episode>"
    return name
