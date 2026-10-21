"""Opaque scope-bound keyset cursors with strict size and type limits."""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from tracecause.api.errors import ApiError


def encode_cursor(scope: str, created: datetime, identifier: UUID) -> str:
    raw = json.dumps([scope, created.isoformat(), str(identifier)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str | None, scope: str) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        if len(value) > 1000:
            raise ValueError("cursor too long")
        raw = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(raw, list) or len(raw) != 3 or raw[0] != scope:
            raise ValueError("cursor scope differs")
        instant = datetime.fromisoformat(raw[1])
        if instant.utcoffset() is None:
            raise ValueError("cursor timezone absent")
        return instant, UUID(raw[2])
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from exc
