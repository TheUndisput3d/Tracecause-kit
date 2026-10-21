"""Full Docker startup ordering, isolated credentials and repeat initialization."""

import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import yaml

from tracecause import full_stack
from tracecause.contracts.models import EpisodeManifest
from tracecause.storage.schema import EpisodeRow, SnapshotRow

ROOT = Path(__file__).resolve().parents[2]


def test_full_stack_includes_all_runtime_services_and_bootstrap_gates():
    config = yaml.safe_load((ROOT / "compose.full.yaml").read_text())
    services = config["services"]
    assert set(services) == {
        "web",
        "api",
        "worker",
        "postgres",
        "neo4j",
        "ollama",
        "bootstrap",
        "migrate",
        "models",
        "seed",
    }
    assert all("profiles" not in service for service in services.values())
    for name in ("api", "worker"):
        service = services[name]
        assert service["environment"]["TRACECAUSE_PUBLIC_READ_ONLY"] == "false"
        assert service["environment"]["TRACECAUSE_ENABLE_LOCAL_MODEL"] == "true"
        assert service["depends_on"]["seed"]["condition"] == "service_completed_successfully"
        assert service["user"] == "10001:10001"
        assert not any("admin_secrets" in mount for mount in service["volumes"])
    assert services["seed"]["depends_on"]["models"]["condition"] == "service_completed_successfully"
    assert (
        services["seed"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    )
    assert services["models"]["depends_on"]["ollama"]["condition"] == "service_healthy"
    assert "worker" in services["web"]["depends_on"]
    assert "artifacts:/var/lib/tracecause/artifacts:ro" in services["api"]["volumes"]
    assert all("ports" not in service for name, service in services.items() if name != "web")
    assert all(port.startswith("127.0.0.1:") for port in services["web"]["ports"])
    assert not config.get("secrets")  # All credentials are initialized in named volumes.


def test_generated_volume_secrets_are_stable_and_never_rotate_silently(tmp_path):
    create = runpy.run_path(str(ROOT / "docker/full/init.py"))["secret"]
    create(tmp_path, "neo4j_auth", "neo4j/")
    path = tmp_path / "neo4j_auth"
    original = path.read_bytes()
    assert original.startswith(b"neo4j/") and len(original) > 32
    assert path.stat().st_mode & 0o777 == 0o444
    create(tmp_path, "neo4j_auth", "neo4j/")
    assert path.read_bytes() == original
    path.chmod(0o644)
    path.write_text("")
    with pytest.raises(ValueError, match="refusing credential rotation"):
        create(tmp_path, "neo4j_auth", "neo4j/")
    assert path.read_bytes() == b""


def test_generated_volume_secret_refuses_symlink(tmp_path):
    create = runpy.run_path(str(ROOT / "docker/full/init.py"))["secret"]
    (tmp_path / "token").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        create(tmp_path, "token")


@pytest.fixture
def existing_seed(tmp_path, monkeypatch):
    """Unit doubles for immutable records; real database upgrade is a release gate."""
    monkeypatch.chdir(ROOT)
    manifest_bytes = (ROOT / "data/demo/episode/manifest.json").read_bytes()
    manifest = EpisodeManifest.model_validate_json(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    result = SimpleNamespace(
        investigation_id=uuid4(),
        episode_id=manifest.episode_id,
        snapshot_id=uuid4(),
        evidence=[SimpleNamespace(evidence_id="saved-source")],
        provenance=SimpleNamespace(input_manifest_hash=manifest_hash),
    )
    episode = SimpleNamespace(
        id=manifest.episode_id,
        workspace_id=manifest.workspace_id,
        ingestion_status="IMPORTED",
        manifest_hash=manifest_hash,
        active_snapshot_id=uuid4(),
    )
    snapshots = {
        identifier: SimpleNamespace(
            id=identifier,
            status="READY",
            episode_id=episode.id,
            workspace_id=episode.workspace_id,
        )
        for identifier in (episode.active_snapshot_id, result.snapshot_id)
    }
    resources = MagicMock()
    resources.investigations.show.return_value = result
    resources.factory.return_value.__enter__.return_value.get.side_effect = (
        lambda model, identifier: (
            episode
            if model is EpisodeRow
            else snapshots.get(identifier)
            if model is SnapshotRow
            else None
        )
    )
    settings = SimpleNamespace(artifact_root=tmp_path)
    marker = tmp_path / "full-stack-seed.json"
    marker.write_text(
        json.dumps(
            {"fingerprint": "previous-corpus", "investigation_id": str(result.investigation_id)}
        )
    )
    operational = [{"episode_id": str(uuid4()), "snapshot_id": str(uuid4())}]
    monkeypatch.setattr(full_stack, "load_settings", lambda path: settings)
    monkeypatch.setattr(full_stack, "open_resources", lambda settings: resources)
    monkeypatch.setattr(full_stack, "seed_fingerprint", lambda: "upgraded-corpus")
    register = MagicMock()
    imports = MagicMock(return_value=operational)
    subprocess = MagicMock(side_effect=AssertionError("must not reseed the existing demo"))
    monkeypatch.setattr(full_stack, "register_bundled_ranker", register)
    monkeypatch.setattr(full_stack, "seed_operational_episodes", imports)
    monkeypatch.setattr(full_stack.subprocess, "run", subprocess)
    return SimpleNamespace(
        resources=resources,
        episode=episode,
        snapshots=snapshots,
        result=result,
        marker=marker,
        operational=operational,
        imports=imports,
        register=register,
        subprocess=subprocess,
    )


@pytest.mark.parametrize("fingerprint", ["previous-corpus", "upgraded-corpus"])
def test_repeat_seed_preserves_existing_demo_across_corpus_updates(
    existing_seed, monkeypatch, fingerprint
):
    state = existing_seed
    active = state.episode.active_snapshot_id
    monkeypatch.setattr(full_stack, "seed_fingerprint", lambda: fingerprint)
    full_stack.seed()
    assert state.episode.active_snapshot_id == active
    assert active != state.result.snapshot_id  # Respect an operator-selected active snapshot.
    state.resources.investigations.investigate.assert_not_called()
    state.subprocess.assert_not_called()
    state.resources.investigations.evidence.assert_called_once_with(
        state.episode.workspace_id, state.result.investigation_id, "saved-source"
    )
    state.imports.assert_called_once()
    assert json.loads(state.marker.read_text()) == {
        "fingerprint": fingerprint,
        "investigation_id": str(state.result.investigation_id),
        "operational_episodes": state.operational,
    }
    state.resources.close.assert_called_once()


@pytest.mark.parametrize(
    "corruption",
    [
        "episode_source",
        "episode_scope",
        "episode_status",
        "result_source",
        "result_episode",
        "active_status",
        "active_scope",
        "saved_status",
        "saved_scope",
        "missing_active",
    ],
)
def test_repeat_seed_rejects_invalid_existing_state_without_reseed(existing_seed, corruption):
    state = existing_seed
    marker_bytes = state.marker.read_bytes()
    if corruption == "episode_source":
        state.episode.manifest_hash = "0" * 64
    elif corruption == "episode_scope":
        state.episode.workspace_id = uuid4()
    elif corruption == "episode_status":
        state.episode.ingestion_status = "FAILED"
    elif corruption == "result_source":
        state.result.provenance.input_manifest_hash = "0" * 64
    elif corruption == "result_episode":
        state.result.episode_id = uuid4()
    elif corruption == "missing_active":
        state.episode.active_snapshot_id = None
    else:
        identifier = (
            state.episode.active_snapshot_id
            if corruption.startswith("active")
            else state.result.snapshot_id
        )
        if corruption.endswith("status"):
            state.snapshots[identifier].status = "RETIRED"
        else:
            state.snapshots[identifier].episode_id = uuid4()
    with pytest.raises(RuntimeError, match="seed marker"):
        full_stack.seed()
    assert state.marker.read_bytes() == marker_bytes
    state.imports.assert_not_called()
    state.subprocess.assert_not_called()
    state.resources.close.assert_called_once()


@pytest.mark.parametrize("failure", ["citation", "new_corpus"])
def test_repeat_seed_failure_does_not_replace_marker(existing_seed, failure):
    state = existing_seed
    marker_bytes = state.marker.read_bytes()
    if failure == "citation":
        state.resources.investigations.evidence.side_effect = RuntimeError("source unavailable")
    else:
        state.imports.side_effect = RuntimeError("source unavailable")
    with pytest.raises(RuntimeError, match="source unavailable"):
        full_stack.seed()
    assert state.marker.read_bytes() == marker_bytes
    state.subprocess.assert_not_called()
    state.resources.close.assert_called_once()
