"""Read-only protocol checks shared by the capture audit and human review importer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .minishop import SCHEDULE_SHA256


def frozen_schedule(path: Path) -> dict[str, Any]:
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != SCHEDULE_SHA256:
        raise ValueError("capture schedule differs from the frozen protocol")
    return dict(json.loads(source))


def timestamp_ns(value: str) -> int:
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None:
        raise ValueError("capture interval must be timezone-aware")
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
