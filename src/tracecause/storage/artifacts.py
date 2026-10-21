"""Immutable content-addressed artifacts with bounded, atomic local writes."""

import hashlib
import io
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(ValueError):
    """Safe artifact-store failure category without user path echoing."""


@dataclass(frozen=True)
class ArtifactDescriptor:
    content_hash: str
    byte_size: int
    relative_key: str


class ArtifactRepository:
    def __init__(self, root: Path, *, max_bytes: int = 100 * 1024 * 1024) -> None:
        if not root.is_absolute() or root == Path("/") or "gold" in root.parts:
            raise ArtifactError("invalid artifact root")
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise ArtifactError("artifact root crosses a symlink")
        if max_bytes < 1:
            raise ArtifactError("artifact byte limit must be positive")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.max_bytes = max_bytes

    def _path(self, content_hash: str) -> Path:
        if not SHA256_RE.fullmatch(content_hash):
            raise ArtifactError("invalid artifact ID")
        parent = self.root / "sha256" / content_hash[:2]
        if any(path.is_symlink() for path in (parent, parent.parent)):
            raise ArtifactError("artifact path crosses a symlink")
        return parent / content_hash

    def put(self, reader: BinaryIO) -> ArtifactDescriptor:
        temporary_root = self.root / "tmp"
        if temporary_root.is_symlink():
            raise ArtifactError("temporary directory is a symlink")
        temporary_root.mkdir(exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="artifact-", dir=temporary_root)
        temporary = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ArtifactError("artifact exceeds byte limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            content_hash = digest.hexdigest()
            final = self._path(content_hash)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.is_symlink():
                raise ArtifactError("artifact is a symlink")
            try:
                os.link(temporary, final)
            except FileExistsError as exc:
                self.verify(content_hash)
                if final.stat().st_size != size:
                    raise ArtifactError("artifact size mismatch") from exc
            # Persist the link and newly created shard directories before SQL
            # can publish a reference to this immutable object.
            for directory in (final.parent, final.parent.parent, self.root):
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return ArtifactDescriptor(
                content_hash, size, f"sha256/{content_hash[:2]}/{content_hash}"
            )
        finally:
            temporary.unlink(missing_ok=True)

    def put_bytes(self, content: bytes) -> ArtifactDescriptor:
        return self.put(io.BytesIO(content))

    def verify(self, content_hash: str) -> int:
        path = self._path(content_hash)
        if path.is_symlink() or not path.is_file():
            raise ArtifactError("artifact missing or unsafe")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != content_hash:
            raise ArtifactError("artifact hash mismatch")
        return size

    def get_bytes(self, content_hash: str) -> bytes:
        size = self.verify(content_hash)
        if size > self.max_bytes:
            raise ArtifactError("artifact exceeds byte limit")
        return self._path(content_hash).read_bytes()

    def iter_lines(self, content_hash: str) -> Iterator[bytes]:
        path = self._path(content_hash)
        if path.is_symlink() or not path.is_file():
            raise ArtifactError("artifact missing or unsafe")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for line in source:
                size += len(line)
                if size > self.max_bytes:
                    raise ArtifactError("artifact exceeds byte limit")
                digest.update(line)
                yield line
        if digest.hexdigest() != content_hash:
            raise ArtifactError("artifact hash mismatch")

    def delete(self, content_hash: str, *, referenced: bool, dry_run: bool = True) -> bool:
        if referenced:
            raise ArtifactError("referenced artifact cannot be deleted")
        self.verify(content_hash)
        if dry_run:
            return False
        self._path(content_hash).unlink()
        return True

    def list_content_hashes(self) -> set[str]:
        directory = self.root / "sha256"
        if not directory.exists():
            return set()
        if directory.is_symlink():
            raise ArtifactError("artifact directory is a symlink")
        hashes: set[str] = set()
        for prefix in directory.iterdir():
            if prefix.is_symlink() or not prefix.is_dir():
                raise ArtifactError("unsafe artifact prefix")
            for path in prefix.iterdir():
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or not SHA256_RE.fullmatch(path.name)
                    or path.name[:2] != prefix.name
                ):
                    raise ArtifactError("unsafe artifact entry")
                hashes.add(path.name)
        return hashes

    def cleanup_temporary(self, *, dry_run: bool = True, minimum_age_seconds: int = 3600) -> int:
        temporary_root = self.root / "tmp"
        if not temporary_root.exists():
            return 0
        if temporary_root.is_symlink():
            raise ArtifactError("temporary directory is a symlink")
        cutoff = time.time() - minimum_age_seconds
        eligible = [
            path
            for path in temporary_root.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.name.startswith("artifact-")
            and path.stat().st_mtime < cutoff
        ]
        if not dry_run:
            for path in eligible:
                path.unlink()
        return len(eligible)
