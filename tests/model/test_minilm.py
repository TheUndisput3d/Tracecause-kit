"""Real pinned MiniLM inference gate; run with the ML dependency group."""

from pathlib import Path

from tracecause.retrieval.embeddings import (
    EMBEDDING_DIMENSION,
    MODEL_REVISION,
    EmbeddingCache,
    MiniLMEmbedder,
    validate_vector,
)


def test_pinned_model_inference_and_cache(tmp_path: Path) -> None:
    assert MODEL_REVISION == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    model = MiniLMEmbedder()
    assert model.dimension == EMBEDDING_DIMENSION == 384
    cache = EmbeddingCache(tmp_path / "vectors", model)
    vectors, hits, misses = cache.encode(["payment timeout", "inventory latency"])
    assert (hits, misses) == (0, 2)
    assert len(vectors) == 2
    for vector in vectors:
        validate_vector(vector, 384)
    assert sum(a * b for a, b in zip(vectors[0], vectors[1], strict=True)) < 1.0
    repeated, hits, misses = cache.encode(["payment timeout", "inventory latency"])
    assert (hits, misses) == (2, 0)
    assert vectors == repeated
