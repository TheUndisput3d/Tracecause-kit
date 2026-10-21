"""Security controls and real process death at artifact/admission boundaries."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tracecause.api.errors import ApiError
from tracecause.api.json_limits import JsonDepthLimit
from tracecause.investigation.slot import GenerationSlot, SlotError
from tracecause.storage.artifacts import ArtifactRepository


def wait_file(path: Path, process: subprocess.Popen) -> None:
    for _ in range(100):
        if path.exists():
            return
        if process.poll() is not None:
            raise AssertionError("child exited before checkpoint")
        time.sleep(0.05)
    raise AssertionError("child did not reach checkpoint")


def test_json_depth_tracks_escapes_across_transport_chunks():
    limiter = JsonDepthLimit(2)
    for chunk in (b'{"a":"[', b"\\", b'"[[[[", "b":', b"[[0]]}"):
        if chunk == b"[[0]]}":
            with pytest.raises(ApiError, match="json_too_deep"):
                limiter.feed(chunk)
        else:
            limiter.feed(chunk)
    shallow = JsonDepthLimit(2)
    shallow.feed(b'{"a": [0], "brackets": "[[[["}')
    assert shallow.depth == 0


def test_generation_fence_survives_sigkill(tmp_path):
    slot = tmp_path / "generation.lock"
    checkpoint = tmp_path / "ready"
    code = """
import signal, sys
from pathlib import Path
from tracecause.investigation.slot import GenerationSlot
with GenerationSlot(Path(sys.argv[1])) as slot:
    slot.mark_dispatch('http://ollama:11434')
    Path(sys.argv[2]).touch()
    signal.pause()
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(slot), str(checkpoint)])
    try:
        wait_file(checkpoint, process)
        with pytest.raises(SlotError, match="busy"):
            with GenerationSlot(slot):
                pytest.fail("concurrent admission")
        process.kill()
        process.wait(timeout=5)
        assert process.returncode == -9
        with pytest.raises(SlotError, match="reconciliation_required"):
            with GenerationSlot(slot):
                pytest.fail("interrupted upstream call admitted")
        assert slot.stat().st_size > 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_interrupted_artifact_write_leaves_no_published_object(tmp_path):
    checkpoint = tmp_path / "ready"
    root = tmp_path / "artifacts"
    code = """
import os, signal, sys
from pathlib import Path
from tracecause.storage.artifacts import ArtifactRepository
def interrupt(*args, **kwargs):
    Path(sys.argv[2]).touch()
    signal.pause()
os.link = interrupt
ArtifactRepository(Path(sys.argv[1])).put_bytes(b'actual-crash-test')
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(root), str(checkpoint)])
    try:
        wait_file(checkpoint, process)
        process.kill()
        process.wait(timeout=5)
        assert not list((root / "sha256").glob("*/*"))
        assert len(list((root / "tmp").glob("artifact-*"))) == 1
        artifacts = ArtifactRepository(root)
        result = artifacts.put_bytes(b"actual-crash-test")
        assert artifacts.verify(result.content_hash) == len(b"actual-crash-test")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_slot_rejects_symlinks_and_shared_inodes(tmp_path):
    source = tmp_path / "source"
    source.write_text("private-data")
    for kind in ("symbolic", "hard"):
        target = tmp_path / kind
        if kind == "symbolic":
            target.symlink_to(source)
        else:
            os.link(source, target)
        with pytest.raises(SlotError, match="unsafe"):
            with GenerationSlot(target):
                pytest.fail("unsafe inode admitted")
    assert source.read_text() == "private-data"


def test_reconciliation_rejects_old_or_foreign_provider_restart(tmp_path, monkeypatch):
    import json
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from scripts.recovery.reconcile_generation import reconcile

    path = tmp_path / "generation.lock"
    with GenerationSlot(path) as slot:
        slot.mark_dispatch("http://ollama:11434")
    (tmp_path / "provider-project").write_text("owned-project")
    state = {
        "Id": "diagnostic-test-double",
        "State": {
            "Running": True,
            "StartedAt": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        "Config": {
            "Labels": {
                "com.docker.compose.service": "ollama",
                "com.docker.compose.project": "owned-project",
            }
        },
    }
    monkeypatch.setattr(
        "scripts.recovery.reconcile_generation.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps([state])),
    )
    with pytest.raises(SlotError, match="restart_not_verified"):
        reconcile(path, "test-double")
    state["State"]["StartedAt"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    state["Config"]["Labels"]["com.docker.compose.project"] = "foreign-project"
    with pytest.raises(SlotError, match="project_not_verified"):
        reconcile(path, "test-double")
    assert path.stat().st_size > 0
