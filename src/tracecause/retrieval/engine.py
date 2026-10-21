"""Scope-safe exact retrieval, rank fusion and bounded graph expansion."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from tracecause.graph.queries import EligibleCall, GraphReader
from tracecause.retrieval.embeddings import Embedder, EmbeddingCache
from tracecause.storage.artifacts import ArtifactRepository

Strategy = Literal["lexical", "dense", "hybrid", "graph"]
MAX_CORPUS = 20_000
MAX_MODALITY = 40
MAX_TOP_K = 60
MAX_QUESTION_CHARS = 2000
RRF_K = 60
TOKEN_PATTERN = r"(?u)\b[\w]+(?:[.:-][\w]+)*\b"


@dataclass(frozen=True)
class RetrievalRequest:
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    as_of_ns: int
    mode: Literal["event", "available"]
    question: str
    symptom_service_key: str | None = None
    top_k: int = 12

    def __post_init__(self) -> None:
        if (
            self.as_of_ns <= 0
            or self.as_of_ns > 2**63 - 1
            or self.mode not in {"event", "available"}
            or not self.question.strip()
            or len(self.question) > MAX_QUESTION_CHARS
            or not 1 <= self.top_k <= MAX_TOP_K
            or self.symptom_service_key is not None
            and not re.fullmatch(r"[0-9a-f]{64}", self.symptom_service_key)
        ):
            raise ValueError("invalid retrieval request")


@dataclass(frozen=True)
class EvidenceCandidate:
    evidence_id: str
    kind: Literal["observation", "chunk"]
    text: str
    service_keys: tuple[str, ...]
    source_locator: dict[str, Any]
    workspace_id: UUID
    episode_id: UUID
    snapshot_id: UUID
    as_of_ns: int
    score: float = 0.0
    score_components: dict[str, float] | None = None


@dataclass(frozen=True)
class RetrievalResult:
    strategy: Strategy
    candidates: tuple[EvidenceCandidate, ...]
    paths: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    trace: dict[str, Any]
    # Internal handoff of the already scoped corpus; never exported by the CLI.
    corpus: tuple[EvidenceCandidate, ...] = field(default=(), repr=False, compare=False)


def corpus_from_reader(
    reader: GraphReader, artifacts: ArtifactRepository, request: RetrievalRequest
) -> tuple[EvidenceCandidate, ...]:
    observations = reader.eligible_observation_summaries(
        workspace_id=request.workspace_id,
        episode_id=request.episode_id,
        snapshot_id=request.snapshot_id,
        as_of_ns=request.as_of_ns,
        mode=request.mode,
    )
    chunks = reader.eligible_document_chunks(
        workspace_id=request.workspace_id,
        episode_id=request.episode_id,
        snapshot_id=request.snapshot_id,
        as_of_ns=request.as_of_ns,
        mode=request.mode,
        artifacts=artifacts,
    )
    if len(observations) + len(chunks) > MAX_CORPUS:
        raise ValueError("eligible retrieval corpus exceeds exact scoring cap")
    candidates: list[EvidenceCandidate] = []
    for row in observations:
        candidates.append(
            EvidenceCandidate(
                evidence_id=f"observation:{row['observation_id']}",
                kind="observation",
                text=row["text"],
                service_keys=tuple(row["service_keys"]),
                source_locator={
                    "observation_id": row["observation_id"],
                    "provenance_hash": row["provenance_hash"],
                    "start_ns": row["start_ns"],
                    "end_ns": row["end_ns"],
                    "available_at": row["available_at"],
                    "supports": row["supports"],
                },
                workspace_id=request.workspace_id,
                episode_id=request.episode_id,
                snapshot_id=request.snapshot_id,
                as_of_ns=request.as_of_ns,
            )
        )
    for row in chunks:
        candidates.append(
            EvidenceCandidate(
                evidence_id=f"chunk:{row['chunk_id']}",
                kind="chunk",
                text=row["text"],
                service_keys=tuple(row["service_keys"]),
                source_locator={
                    "document_id": row["document_id"],
                    "chunk_id": row["chunk_id"],
                    "source_id": row["source_id"],
                    "source_hash": row["source_hash"],
                    "title": row["title"],
                    "license": row["license"],
                    "published_at": row["published_at"],
                    "available_at": row["available_at"],
                    "start_char": row["start_char"],
                    "end_char": row["end_char"],
                    "verified_assertions": row.get("verified_assertions", []),
                },
                workspace_id=request.workspace_id,
                episode_id=request.episode_id,
                snapshot_id=request.snapshot_id,
                as_of_ns=request.as_of_ns,
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.evidence_id))


def _embedding_text(item: EvidenceCandidate) -> str:
    if item.kind == "chunk":
        return f"{item.source_locator['title']}. {item.text}"
    return item.text


def lexical_rank(
    question: str, corpus: tuple[EvidenceCandidate, ...], limit: int = MAX_MODALITY
) -> tuple[tuple[str, float], ...]:
    if not corpus:
        return ()
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]

    texts = [_embedding_text(item) for item in corpus]
    vectorizer = TfidfVectorizer(token_pattern=TOKEN_PATTERN, lowercase=True, norm="l2")
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:  # All eligible texts contain no indexable terms.
        return ()
    query = vectorizer.transform([question])
    scores = (matrix @ query.T).toarray().ravel()
    return tuple(
        sorted(
            (
                (item.evidence_id, float(score))
                for item, score in zip(corpus, scores, strict=True)
                if score > 0
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )[:limit]
    )


def dense_rank(
    question: str,
    corpus: tuple[EvidenceCandidate, ...],
    cache: EmbeddingCache,
    limit: int = MAX_MODALITY,
) -> tuple[tuple[tuple[str, float], ...], dict[str, int]]:
    if not corpus:
        return (), {"hits": 0, "misses": 0}
    vectors, hits, misses = cache.encode([question, *(_embedding_text(item) for item in corpus)])
    query = vectors[0]
    scores = (
        (item.evidence_id, sum(a * b for a, b in zip(query, vector, strict=True)))
        for item, vector in zip(corpus, vectors[1:], strict=True)
    )
    return (
        tuple(sorted(scores, key=lambda pair: (-pair[1], pair[0]))[:limit]),
        {"hits": hits, "misses": misses},
    )


def rrf_fuse(
    modalities: dict[str, tuple[tuple[str, float], ...]],
) -> tuple[tuple[str, float, dict[str, float]], ...]:
    totals: dict[str, float] = defaultdict(float)
    components: dict[str, dict[str, float]] = defaultdict(dict)
    for modality in sorted(modalities):
        seen: set[str] = set()
        for rank, (evidence_id, raw_score) in enumerate(modalities[modality], start=1):
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            contribution = 1.0 / (RRF_K + rank)
            totals[evidence_id] += contribution
            components[evidence_id][f"{modality}_rank"] = float(rank)
            components[evidence_id][f"{modality}_score"] = raw_score
            components[evidence_id][f"{modality}_rrf"] = contribution
    return tuple(
        (evidence_id, score, components[evidence_id])
        for evidence_id, score in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    )


def expand_graph(
    calls: tuple[EligibleCall, ...],
    seeds: tuple[str, ...],
    eligible: dict[str, EvidenceCandidate],
) -> tuple[dict[str, float], tuple[dict[str, Any], ...], bool]:
    """Walk at most three directed CALLS hops, retaining per-hop support IDs."""
    adjacent: dict[str, list[EligibleCall]] = defaultdict(list)
    for call in calls:
        adjacent[call.caller_service_key].append(call)
        adjacent[call.callee_service_key].append(call)
    for entries in adjacent.values():
        entries.sort(key=lambda item: (item.caller_service_key, item.callee_service_key))
    reached: set[str] = set(seeds)
    nodes: set[str] = set(seeds)
    edges: set[tuple[str, str]] = set()
    evidence: dict[str, float] = {}
    paths: list[dict[str, Any]] = []
    pending: deque[tuple[str, tuple[str, ...], tuple[dict[str, Any], ...]]] = deque(
        (seed, (seed,), ()) for seed in seeds
    )
    truncated = len(reached) > 20 or len(nodes) > 80
    visited_states = 0
    while pending and not truncated:
        visited_states += 1
        if visited_states > 500 or len(pending) > 500:
            truncated = True
            break
        current, service_path, steps = pending.popleft()
        if len(steps) >= 3:
            continue
        for call in adjacent[current]:
            neighbor = (
                call.callee_service_key
                if call.caller_service_key == current
                else call.caller_service_key
            )
            if neighbor in service_path:
                continue
            edge_key = (call.caller_service_key, call.callee_service_key)
            support_ids = tuple(
                f"observation:{value}"
                for value in call.support_observation_ids
                if f"observation:{value}" in eligible
            )
            if not support_ids:
                continue
            new_nodes = nodes | {neighbor}
            new_edges = edges | {edge_key}
            new_reached = reached | {neighbor}
            new_evidence = set(evidence) | set(support_ids)
            if (
                len(new_reached) > 20
                or len(new_nodes) > 80
                or len(new_edges) > 120
                or len(new_evidence) > 60
            ):
                truncated = True
                break
            nodes, edges, reached = new_nodes, new_edges, new_reached
            step = {
                "caller_service_key": call.caller_service_key,
                "callee_service_key": call.callee_service_key,
                "traversal_direction": (
                    "forward" if call.caller_service_key == current else "reverse"
                ),
                "support_observation_ids": support_ids,
            }
            new_steps = (*steps, step)
            for evidence_id in support_ids:
                evidence[evidence_id] = max(evidence.get(evidence_id, 0.0), 1.0 / len(new_steps))
            paths.append({"services": (*service_path, neighbor), "edges": new_steps})
            if len(paths) >= 80:
                truncated = True
                break
            if neighbor not in service_path and len(new_steps) < 3:
                pending.append((neighbor, (*service_path, neighbor), new_steps))
    return evidence, tuple(paths[:80]), truncated or len(paths) > 80


def retrieve(
    reader: GraphReader,
    artifacts: ArtifactRepository,
    request: RetrievalRequest,
    *,
    strategy: Strategy,
    embedder: Embedder | None = None,
    embedding_cache_root: Path | None = None,
) -> RetrievalResult:
    if strategy not in {"lexical", "dense", "hybrid", "graph"}:
        raise ValueError("unknown retrieval strategy")
    start = time.perf_counter()
    corpus = corpus_from_reader(reader, artifacts, request)
    loaded_ms = round((time.perf_counter() - start) * 1000, 3)
    by_id = {item.evidence_id: item for item in corpus}
    lexical: tuple[tuple[str, float], ...] = ()
    dense: tuple[tuple[str, float], ...] = ()
    cache_state = {"hits": 0, "misses": 0}
    if strategy in {"lexical", "hybrid", "graph"}:
        lexical = lexical_rank(request.question, corpus)
    if strategy in {"dense", "hybrid", "graph"}:
        if embedder is None or embedding_cache_root is None:
            raise ValueError("dense strategy requires an embedder and cache root")
        cache = EmbeddingCache(embedding_cache_root, embedder)
        dense, cache_state = dense_rank(request.question, corpus, cache)
    ranked = rrf_fuse(
        {
            name: items
            for name, items in (("dense", dense), ("lexical", lexical))
            if name == strategy or strategy in {"hybrid", "graph"}
        }
    )
    components = {evidence_id: (score, details) for evidence_id, score, details in ranked}
    paths: tuple[dict[str, Any], ...] = ()
    warnings: list[str] = []
    if not any(item.kind == "chunk" for item in corpus):
        warnings.append("no_eligible_documents")
    if not corpus:
        warnings.append("no_eligible_evidence")
    if strategy == "graph":
        seed_services = list(
            dict.fromkeys(
                [
                    *([request.symptom_service_key] if request.symptom_service_key else []),
                    *(
                        key
                        for evidence_id, _, _ in ranked[:MAX_MODALITY]
                        for key in by_id[evidence_id].service_keys
                    ),
                ]
            )
        )
        if len(seed_services) > 20:
            warnings.append("graph_truncated")
            seed_services = seed_services[:20]
        calls, edges_truncated = reader.eligible_calls_bounded(
            workspace_id=request.workspace_id,
            episode_id=request.episode_id,
            snapshot_id=request.snapshot_id,
            as_of_ns=request.as_of_ns,
            mode=request.mode,
        )
        graph_scores, paths, truncated = expand_graph(calls, tuple(seed_services), by_id)
        if (truncated or edges_truncated) and "graph_truncated" not in warnings:
            warnings.append("graph_truncated")
        for evidence_id, graph_score in graph_scores.items():
            old_score, old_details = components.get(evidence_id, (0.0, {}))
            components[evidence_id] = (
                old_score + graph_score / (RRF_K + 1),
                {**old_details, "graph_support": graph_score},
            )
    ordered = sorted(components.items(), key=lambda pair: (-pair[1][0], pair[0]))
    candidates = tuple(
        replace(by_id[evidence_id], score=score, score_components=details)
        for evidence_id, (score, details) in ordered[: request.top_k]
    )
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    trace = {
        "strategy": strategy,
        "snapshot_id": str(request.snapshot_id),
        "as_of_ns": request.as_of_ns,
        "mode": request.mode,
        "eligible_count": len(corpus),
        "observation_count": sum(item.kind == "observation" for item in corpus),
        "document_chunk_count": sum(item.kind == "chunk" for item in corpus),
        "lexical_ranked_ids": [item[0] for item in lexical],
        "dense_ranked_ids": [item[0] for item in dense],
        "returned_ids": [item.evidence_id for item in candidates],
        "returned_components": [
            {"evidence_id": item.evidence_id, "components": item.score_components}
            for item in candidates
        ],
        "embedding_version": embedder.version if dense and embedder is not None else None,
        "cache": cache_state,
        "load_ms": loaded_ms,
        "total_ms": elapsed_ms,
        "warnings": warnings,
    }
    return RetrievalResult(strategy, candidates, paths, tuple(warnings), trace, corpus)


def result_jsonable(result: RetrievalResult) -> dict[str, Any]:
    return {
        "strategy": result.strategy,
        "candidates": [asdict(candidate) for candidate in result.candidates],
        "paths": result.paths,
        "warnings": result.warnings,
        "trace": result.trace,
    }
