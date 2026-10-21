"""Pinned CPU MiniLM vectors with a verified, input-addressed local cache."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Protocol, cast

from tracecause.documents.markdown import TOKENIZER_REVISION, TOKENIZER_SHA256, model_tokenizer

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
EMBEDDING_DIMENSION = 384
EMBEDDING_VERSION = f"{MODEL_ID}@{MODEL_REVISION}:tokenizer@{TOKENIZER_REVISION}"
MAX_EMBEDDING_TEXT = 4096


class EmbeddingError(ValueError):
    """A local model or cached vector cannot satisfy the pinned contract."""


class Embedder(Protocol):
    dimension: int
    version: str

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]: ...


def validate_vector(vector: tuple[float, ...], dimension: int) -> None:
    if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
        raise EmbeddingError("embedding has invalid dimension or values")
    if not math.isclose(sum(value * value for value in vector), 1.0, abs_tol=0.001):
        raise EmbeddingError("embedding is not unit normalized")


class MiniLMEmbedder:
    dimension = EMBEDDING_DIMENSION
    version = EMBEDDING_VERSION

    def __init__(self) -> None:
        model_tokenizer()  # Verify the bundled tokenizer bytes and context metadata.
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                MODEL_ID,
                revision=MODEL_REVISION,
                trust_remote_code=False,
                local_files_only=True,
                device="cpu",
            )
        except (ImportError, OSError, ValueError) as exc:
            raise EmbeddingError("pinned local MiniLM model is unavailable") from exc
        if self._model.get_embedding_dimension() != self.dimension:
            raise EmbeddingError("pinned model dimension differs")

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        if any(not text.strip() or len(text) > MAX_EMBEDDING_TEXT for text in texts):
            raise EmbeddingError("embedding text is empty or too long")
        matrix = self._model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        result = [tuple(float(value) for value in row) for row in matrix]
        for vector in result:
            validate_vector(vector, self.dimension)
        return result


class EmbeddingCache:
    def __init__(self, root: Path, embedder: Embedder) -> None:
        if not root.is_absolute() or root == Path("/") or "gold" in root.parts:
            raise EmbeddingError("invalid embedding cache root")
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise EmbeddingError("embedding cache root crosses a symlink")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.embedder = embedder
        if embedder.dimension != EMBEDDING_DIMENSION:
            raise EmbeddingError("embedding model has wrong dimension")

    def _key(self, text: str) -> str:
        material = json.dumps(
            {"version": self.embedder.version, "tokenizer_sha256": TOKENIZER_SHA256, "text": text},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(material).hexdigest()

    def _path(self, key: str) -> Path:
        folder = self.root / key[:2]
        if folder.is_symlink():
            raise EmbeddingError("embedding cache path is a symlink")
        return folder / key

    def encode(self, texts: list[str]) -> tuple[list[tuple[float, ...]], int, int]:
        vectors: list[tuple[float, ...] | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}
        misses: dict[str, str] = {}
        hit_count = 0
        for index, value in enumerate(texts):
            if not value.strip() or len(value) > MAX_EMBEDDING_TEXT:
                raise EmbeddingError("embedding text is empty or too long")
            key = self._key(value)
            path = self._path(key)
            if path.exists():
                if path.is_symlink() or path.stat().st_size > 32_000:
                    raise EmbeddingError("embedding cache entry is unsafe")
                try:
                    payload = json.loads(path.read_bytes())
                    if payload["key"] != key or payload["version"] != self.embedder.version:
                        raise ValueError("cache identity differs")
                    vector = tuple(float(number) for number in payload["vector"])
                    validate_vector(vector, self.embedder.dimension)
                except (ValueError, TypeError, KeyError) as exc:
                    raise EmbeddingError("embedding cache entry is invalid") from exc
                vectors[index] = vector
                hit_count += 1
            else:
                missing.setdefault(key, []).append(index)
                misses[key] = value
        if missing:
            keys = list(missing)
            encoded = self.embedder.encode([misses[key] for key in keys])
            if len(encoded) != len(keys):
                raise EmbeddingError("embedding provider returned wrong batch size")
            for key, vector in zip(keys, encoded, strict=True):
                validate_vector(vector, self.embedder.dimension)
                payload = json.dumps(
                    {"key": key, "version": self.embedder.version, "vector": vector},
                    separators=(",", ":"),
                ).encode()
                path = self._path(key)
                path.parent.mkdir(exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix="embedding-", dir=path.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(payload)
                        output.flush()
                        os.fsync(output.fileno())
                    try:
                        os.link(temporary, path)
                    except FileExistsError:
                        if path.is_symlink():
                            raise EmbeddingError("embedding cache entry is unsafe") from None
                finally:
                    temporary.unlink(missing_ok=True)
                for index in missing[key]:
                    vectors[index] = vector
        return [cast(tuple[float, ...], value) for value in vectors], hit_count, len(missing)
