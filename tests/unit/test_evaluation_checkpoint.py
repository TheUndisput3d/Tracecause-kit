"""Durable checkpoint publication preserves prior output on a failed rename."""

import json
import os
from pathlib import Path

import pytest
from scripts.evaluation.prepare import write_json


def test_checkpoint_syncs_file_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fsync
    calls = []

    def synced(descriptor: int) -> None:
        calls.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", synced)
    output = tmp_path / "checkpoint.json"
    write_json(output, {"done": 3})
    assert json.loads(output.read_bytes()) == {"done": 3}
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == [output]


def test_checkpoint_failed_replace_keeps_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "checkpoint.json"
    write_json(output, {"done": 2})

    def failed(*args) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", failed)
    with pytest.raises(OSError, match="rename failure"):
        write_json(output, {"done": 3})
    assert json.loads(output.read_bytes()) == {"done": 2}
    assert list(tmp_path.iterdir()) == [output]
