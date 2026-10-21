"""Isolation guards for the opt-in real Docker fresh/restore drill."""

from unittest.mock import MagicMock

import pytest
from scripts.deployment import full_recovery
from scripts.recovery.operational_audit import digest


@pytest.mark.parametrize(
    "project",
    [
        "tracecause-full",
        "existing",
        "tracecause-proof-",
        "tracecause-proof-X",
        "tracecause-proof-$(id)",
    ],
)
def test_drill_requires_owned_unique_project_prefix(monkeypatch, project):
    command = MagicMock()
    monkeypatch.setattr(full_recovery, "output", command)
    with pytest.raises(ValueError, match="unique"):
        full_recovery.new_project(project)
    command.assert_not_called()


@pytest.mark.parametrize("existing", ["containers", "volumes"])
def test_drill_never_reuses_existing_application_state(monkeypatch, existing):
    command = MagicMock(
        side_effect=["container-id"]
        if existing == "containers"
        else ["", "tracecause-proof-unit_pg_data"]
    )
    monkeypatch.setattr(full_recovery, "output", command)
    with pytest.raises(ValueError, match="existing"):
        full_recovery.new_project("tracecause-proof-unit")


@pytest.mark.parametrize("ports", [(14001, 14001), (80, 14001), (65536, 14001)])
def test_drill_ports_must_be_distinct_and_unprivileged(ports):
    with pytest.raises(ValueError, match="distinct"):
        full_recovery.check_ports(ports)


def test_recovery_comparison_hash_is_key_order_independent():
    assert digest({"scope": 1, "source": "a"}) == digest({"source": "a", "scope": 1})
    assert digest({"scope": 1, "source": "a"}) != digest({"scope": 1, "source": "b"})


def test_recovery_resume_rejects_unowned_project_before_reading(tmp_path):
    with pytest.raises(ValueError, match="owned drill"):
        full_recovery.validate_resume(tmp_path, "tracecause-full")


@pytest.mark.parametrize("completed", ["before.json", "backup", "after.json", "report.json"])
def test_recovery_resume_refuses_started_restore_state(tmp_path, completed):
    (tmp_path / completed).touch()
    with pytest.raises(ValueError, match="already-started"):
        full_recovery.validate_resume(tmp_path, "tracecause-proof-unit")
