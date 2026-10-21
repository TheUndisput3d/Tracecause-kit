"""Public-only bootstrap bundle membership, hashes and temporal cursor bounds."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from tracecause.operational import seed as seed_module
from tracecause.operational.models import ServiceRef
from tracecause.operational.replay import REPLAY_VERSION
from tracecause.operational.seed import bundled_episodes
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import EpisodeRow, SnapshotRow


def bundle(root: Path, *, hour: int) -> dict[str, str]:
    identifier = str(uuid4())
    directory = root / "episodes" / identifier
    directory.mkdir(parents=True)
    source = json.dumps(
        {
            "schema_version": "1.0",
            "application_id": "admission-fixture",
            "application_name": "Unit test admission fixture",
            "topology": "single service",
            "events": [
                {
                    "id": str(uuid4()),
                    "kind": "ownership",
                    "occurred_at": f"2026-10-19T{hour:02d}:00:00Z",
                    "available_at": f"2026-10-19T{hour:02d}:00:00Z",
                    "service": {"namespace": "admission", "environment": "test", "name": "api"},
                    "source": "unit test operator assignment",
                    "details": {"team": "fixture-owners"},
                }
            ],
        }
    ).encode()
    (directory / "operational.json").write_bytes(source)
    manifest = {
        "schema_version": "1.0",
        "episode_id": identifier,
        "workspace_id": str(DEFAULT_WORKSPACE_ID),
        "display_name": "Bootstrap boundary fixture",
        "origin": "synthetic",
        "event_interval": {
            "start": f"2026-10-19T{hour:02d}:00:00Z",
            "end": f"2026-10-19T{hour:02d}:05:00Z",
        },
        "baseline_interval": None,
        "availability_recorded": False,
        "sources": [
            {
                "logical_name": "operational.json",
                "kind": "operational_json",
                "bytes": len(source),
                "sha256": hashlib.sha256(source).hexdigest(),
                "published_at": None,
            }
        ],
        "public_notes": "Unit test only.",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return {
        "episode_id": identifier,
        "analysis_end": f"2026-10-19T{hour:02d}:04:00Z",
        "symptom_service": "api",
    }


def test_seed_uses_chronological_public_metadata(tmp_path: Path) -> None:
    newer, earlier = bundle(tmp_path, hour=2), bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [newer, earlier]}))
    values = bundled_episodes(tmp_path)
    assert [str(item.manifest.episode_id) for item in values] == [
        earlier["episode_id"],
        newer["episode_id"],
    ]
    assert values[0].default_as_of.hour == 1
    assert values[0].default_symptom_service_id == ServiceRef(
        namespace="admission", environment="test", name="api"
    ).key(DEFAULT_WORKSPACE_ID)


@pytest.mark.parametrize("symptom", ["missing", ""])
def test_seed_requires_public_symptom_identity_in_receipt(tmp_path, symptom):
    entry = bundle(tmp_path, hour=1)
    entry["symptom_service"] = symptom
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    with pytest.raises(ValueError, match="symptom service"):
        bundled_episodes(tmp_path)


def test_seed_rejects_source_tampering(tmp_path: Path) -> None:
    entry = bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    (tmp_path / "episodes" / entry["episode_id"] / "operational.json").write_text("changed")
    with pytest.raises(ValueError, match="source differs"):
        bundled_episodes(tmp_path)


@pytest.mark.parametrize("cursor", ["2026-10-19T02:00:00Z", "2026-10-19T01:04:00"])
def test_seed_rejects_out_of_scope_or_naive_demo_cursor(tmp_path: Path, cursor: str) -> None:
    entry = bundle(tmp_path, hour=1)
    entry["analysis_end"] = cursor
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    with pytest.raises(ValueError, match="cursor"):
        bundled_episodes(tmp_path)


def test_seed_refuses_undeclared_private_metadata(tmp_path: Path) -> None:
    entry = bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    (tmp_path / "episodes" / entry["episode_id"] / "truth.json").write_text("{}")
    with pytest.raises(ValueError, match="undeclared"):
        bundled_episodes(tmp_path)


def test_seed_refuses_duplicate_episode_membership(tmp_path: Path) -> None:
    entry = bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry, entry]}))
    with pytest.raises(ValueError, match="membership"):
        bundled_episodes(tmp_path)


@pytest.mark.parametrize("corruption", [None, "material_hash", "projection"])
def test_existing_seed_verifies_complete_pinned_projection(tmp_path, monkeypatch, corruption):
    entry = bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    source = bundled_episodes(tmp_path)[0]
    snapshot_id = uuid4()
    membership = {
        "version": 1,
        "operational_hashes": ["a" * 64],
        "operational_replay_version": REPLAY_VERSION,
        "operational_replay_modes": ["event"],
        "document_scope_version": seed_module.DOCUMENT_SCOPE_VERSION,
    }
    episode = SimpleNamespace(
        id=source.manifest.episode_id,
        workspace_id=source.manifest.workspace_id,
        ingestion_status="IMPORTED",
        manifest_hash=source.manifest_hash,
        active_snapshot_id=snapshot_id,
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        workspace_id=episode.workspace_id,
        episode_id=episode.id,
        status="READY",
        membership=membership,
        content_hash="b" * 64,
    )
    resources = MagicMock()
    session = resources.factory.return_value.__enter__.return_value
    session.get.side_effect = lambda model, key: (
        episode if model is EpisodeRow else snapshot if model is SnapshotRow else None
    )
    material = SimpleNamespace(content_hash="c" * 64 if corruption == "material_hash" else "b" * 64)
    load = MagicMock(return_value=material)
    verify = MagicMock()
    if corruption == "projection":
        verify.side_effect = RuntimeError("projection differs")
    lease = MagicMock(return_value=nullcontext())
    monkeypatch.setattr(seed_module, "load_graph_material", load)
    monkeypatch.setattr(seed_module, "validate_projection", verify)
    monkeypatch.setattr(seed_module, "reader_lease", lease)
    if corruption is None:
        assert seed_module._verify_ready(resources, source) == snapshot_id
    else:
        with pytest.raises(RuntimeError, match="differs"):
            seed_module._verify_ready(resources, source)
    load.assert_called_once_with(
        session,
        episode.workspace_id,
        episode.id,
        artifacts=resources.artifacts,
        membership=membership,
    )
    lease.assert_called_once_with(
        resources.factory, episode.workspace_id, episode.id, snapshot_id, renew=True
    )
    if corruption != "material_hash":
        verify.assert_called_once_with(resources.driver, snapshot_id, material, complete=True)
    else:
        verify.assert_not_called()
    assert episode.active_snapshot_id == snapshot_id


def test_existing_seed_upgrades_old_replay_without_mutating_old_snapshot(tmp_path, monkeypatch):
    entry = bundle(tmp_path, hour=1)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [entry]}))
    source = bundled_episodes(tmp_path)[0]
    old_id, new_id = uuid4(), uuid4()
    old_membership = {"version": 1, "operational_hashes": ["a" * 64]}
    episode = SimpleNamespace(
        id=source.manifest.episode_id,
        workspace_id=source.manifest.workspace_id,
        ingestion_status="IMPORTED",
        manifest_hash=source.manifest_hash,
        active_snapshot_id=old_id,
    )
    snapshots = {
        identifier: SimpleNamespace(
            id=identifier,
            workspace_id=episode.workspace_id,
            episode_id=episode.id,
            status="READY",
            content_hash=str(identifier),
            membership=old_membership
            if identifier == old_id
            else {
                **old_membership,
                "operational_replay_version": REPLAY_VERSION,
                "operational_replay_modes": ["event"],
                "document_scope_version": seed_module.DOCUMENT_SCOPE_VERSION,
            },
        )
        for identifier in (old_id, new_id)
    }
    resources = MagicMock()
    resources.factory.return_value.__enter__.return_value.get.side_effect = lambda model, key: (
        episode if model is EpisodeRow else snapshots[key]
    )
    monkeypatch.setattr(seed_module, "reader_lease", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        seed_module,
        "load_graph_material",
        lambda *args, **kwargs: SimpleNamespace(content_hash=str(episode.active_snapshot_id)),
    )
    verify = MagicMock()
    replay = MagicMock()
    build = MagicMock(
        side_effect=lambda *args, **kwargs: setattr(episode, "active_snapshot_id", new_id)
    )
    monkeypatch.setattr(seed_module, "validate_projection", verify)
    monkeypatch.setattr(seed_module, "materialize_replay", replay)
    monkeypatch.setattr(seed_module, "build_snapshot", build)
    assert seed_module._verify_ready(resources, source) == new_id
    replay.assert_called_once_with(
        resources.factory, resources.artifacts, episode.workspace_id, episode.id
    )
    build.assert_called_once()
    assert verify.call_count == 2
    assert snapshots[old_id].status == "READY"
    assert snapshots[old_id].membership == old_membership
    assert "operational_replay_version" not in old_membership
    assert seed_module._verify_ready(resources, source) == new_id
    assert replay.call_count == build.call_count == 1
