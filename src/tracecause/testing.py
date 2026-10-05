"""Deterministic helpers for tests and fixture generation."""

from datetime import UTC, datetime
from random import Random

FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def seeded_random(seed: int = 0) -> Random:
    return Random(seed)
