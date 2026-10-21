"""Resolve only files declared by a validated public episode manifest."""

from pathlib import Path

from tracecause.contracts.models import EpisodeManifest


class UnsafeImportDirectory(ValueError):
    """The import directory contains an undeclared or unsafe file."""


def resolve_episode_sources(directory: Path, manifest: EpisodeManifest) -> tuple[Path, ...]:
    """Require an exact flat allowlist before an importer opens any source file."""
    if "gold" in directory.resolve().parts:
        raise UnsafeImportDirectory("evaluator directory is not importable")
    if any(path.is_symlink() for path in (directory, *directory.parents)) or not directory.is_dir():
        raise UnsafeImportDirectory("episode directory must be a real directory")
    names = [source.logical_name for source in manifest.sources]
    if len(names) != len(set(names)):
        raise UnsafeImportDirectory("duplicate source names")
    expected = {"manifest.json", *names}
    actual = {entry.name for entry in directory.iterdir()}
    if actual != expected:
        raise UnsafeImportDirectory("episode directory contains missing or undeclared files")
    paths = []
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise UnsafeImportDirectory("declared source is not a regular file")
        paths.append(path)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise UnsafeImportDirectory("manifest is not a regular file")
    return tuple(paths)
