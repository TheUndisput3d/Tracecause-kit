"""Public source allowlisting and runtime/evaluator module isolation."""

import ast
import json
from pathlib import Path

import pytest

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.source_allowlist import UnsafeImportDirectory, resolve_episode_sources


def test_smoke_fixture_import_allowlist() -> None:
    root = Path(__file__).resolve().parents[2] / "data/fixtures/smoke"
    episode_id = json.loads((root / "index.json").read_text())["episodes"][0]["episode_id"]
    directory = root / episode_id
    manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
    assert [path.name for path in resolve_episode_sources(directory, manifest)] == ["traces.jsonl"]


def test_allowlist_rejects_private_sidecars_and_symlinks(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "data/fixtures/smoke"
    episode_id = json.loads((root / "index.json").read_text())["episodes"][0]["episode_id"]
    original = root / episode_id
    directory = tmp_path / "episode"
    directory.mkdir()
    for name in ("manifest.json", "traces.jsonl"):
        (directory / name).write_bytes((original / name).read_bytes())
    manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
    (directory / "truth.json").write_text("{}")
    with pytest.raises(UnsafeImportDirectory, match="undeclared"):
        resolve_episode_sources(directory, manifest)
    (directory / "truth.json").unlink()
    (directory / "traces.jsonl").unlink()
    (directory / "traces.jsonl").symlink_to(original / "traces.jsonl")
    with pytest.raises(UnsafeImportDirectory, match="regular file"):
        resolve_episode_sources(directory, manifest)


def test_allowlist_refuses_evaluator_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "data/fixtures/smoke"
    episode_id = json.loads((root / "index.json").read_text())["episodes"][0]["episode_id"]
    original = root / episode_id
    directory = tmp_path / "gold" / "episode"
    directory.parent.mkdir()
    directory.mkdir()
    for name in ("manifest.json", "traces.jsonl"):
        (directory / name).write_bytes((original / name).read_bytes())
    manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
    with pytest.raises(UnsafeImportDirectory, match="evaluator"):
        resolve_episode_sources(directory, manifest)


def test_runtime_never_imports_evaluator_modules() -> None:
    runtime = Path(__file__).resolve().parents[2] / "src/tracecause"
    for module in runtime.rglob("*.py"):
        tree = ast.parse(module.read_text())
        imports = [
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(name.startswith("scripts.fixtures") for name in imports), module
        assert "data/gold" not in module.read_text(), module
