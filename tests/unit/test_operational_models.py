"""Invalid operational records fail admission before they can poison history views."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tracecause.operational.models import OperationalEvent

ROOT = Path(__file__).resolve().parents[2]


def event(kind):
    records = json.loads(
        (
            ROOT
            / "data/operational-lab/episodes/5ff7a513-f40a-4275-b3d0-dae846cbbd99/operational.json"
        ).read_bytes()
    )["events"]
    return copy.deepcopy(next(row for row in records if row["kind"] == kind))


@pytest.mark.parametrize("value", [None, True, 1.5, "123", 0, -1, 2**31])
def test_runtime_process_requires_a_positive_integer(value):
    row = event("runtime")
    row["details"]["process_id"] = value
    with pytest.raises(ValidationError, match="positive integer"):
        OperationalEvent.model_validate(row)


@pytest.mark.parametrize("key", ["a" * 201, "access_token"])
def test_configuration_keys_fit_history_and_exclude_secrets(key):
    row = event("configuration")
    row["details"]["key"] = key
    with pytest.raises(ValidationError):
        OperationalEvent.model_validate(row)


def test_nonfinite_configuration_values_are_rejected():
    row = event("configuration")
    row["details"]["value"] = float("nan")
    with pytest.raises(ValidationError):
        OperationalEvent.model_validate(row)


def test_runtime_type_has_a_closed_allowlist():
    row = event("runtime")
    row["details"]["runtime_type"] = "pretend-host"
    with pytest.raises(ValidationError, match="runtime type"):
        OperationalEvent.model_validate(row)
