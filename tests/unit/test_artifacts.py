"""Artifact storage must be content addressed and path safe."""

import hashlib
from pathlib import Path

import pytest

from tracecause.storage.artifacts import ArtifactError, ArtifactRepository


def test_artifact_put_verify_repeat_and_reference_guard(tmp_path: Path) -> None:
    store = ArtifactRepository(tmp_path / "artifacts")
    content = b"redacted normalized telemetry\n"
    first = store.put_bytes(content)
    second = store.put_bytes(content)
    assert first == second
    assert first.content_hash == hashlib.sha256(content).hexdigest()
    assert store.verify(first.content_hash) == len(content)
    assert store.list_content_hashes() == {first.content_hash}
    assert store.get_bytes(first.content_hash) == content
    assert store.delete(first.content_hash, referenced=False, dry_run=True) is False
    with pytest.raises(ArtifactError, match="referenced"):
        store.delete(first.content_hash, referenced=True, dry_run=False)
    assert store.delete(first.content_hash, referenced=False, dry_run=False)
    assert store.list_content_hashes() == set()
    with pytest.raises(ArtifactError, match="missing"):
        store.get_bytes(first.content_hash)


def test_artifact_rejects_traversal_symlink_and_tamper(tmp_path: Path) -> None:
    store = ArtifactRepository(tmp_path / "artifacts")
    with pytest.raises(ArtifactError, match="invalid artifact ID"):
        store.get_bytes("../outside")
    item = store.put_bytes(b"safe")
    path = store.root / item.relative_key
    path.write_bytes(b"tampered")
    with pytest.raises(ArtifactError, match="hash mismatch"):
        store.get_bytes(item.content_hash)
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(ArtifactError, match="unsafe"):
        store.get_bytes(item.content_hash)


def test_artifact_byte_limit_and_evaluator_root(tmp_path: Path) -> None:
    store = ArtifactRepository(tmp_path / "artifacts", max_bytes=3)
    with pytest.raises(ArtifactError, match="byte limit"):
        store.put_bytes(b"four")
    assert not list((store.root / "tmp").iterdir())
    with pytest.raises(ArtifactError, match="invalid artifact root"):
        ArtifactRepository(tmp_path / "gold" / "artifacts")
