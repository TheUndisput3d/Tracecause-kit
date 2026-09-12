"""Bounded presentation of already source-validated knowledge, without inference."""

from collections import deque
from typing import Any, Literal

from tracecause.contracts.models import (
    GraphEdge,
    GraphResponse,
    KnowledgeCheck,
    KnowledgeChecksResponse,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeResponse,
)


def assemble_knowledge(
    graph: GraphResponse,
    observations: tuple[dict[str, Any], ...],
    chunks: tuple[dict[str, Any], ...],
) -> KnowledgeResponse:
    names = {node.service_id: node.display_name for node in graph.nodes}
    nodes = {
        "service:" + key: KnowledgeNode(
            node_id="service:" + key, kind="service", label=name[:500], evidence_ids=[]
        )
        for key, name in names.items()
    }
    edges = [
        KnowledgeEdge(
            source="service:" + edge.source_service_id,
            target="service:" + edge.target_service_id,
            relationship="CALLS",
            evidence_ids=edge.supporting_evidence_ids,
        )
        for edge in graph.edges
    ]
    eligible = [row for row in observations if set(row["service_keys"]) <= names.keys()]
    # Round robin by subject gives each service/dependency a recent observation.
    subjects: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in sorted(eligible, key=lambda r: (-r["end_ns"], r["observation_id"])):
        subjects.setdefault(tuple(row["service_keys"]), []).append(row)
    chosen: list[dict[str, Any]] = []
    for index in range(24):
        for key in sorted(subjects):
            if index < len(subjects[key]) and len(chosen) < 24:
                chosen.append(subjects[key][index])
        if len(chosen) == 24:
            break
    for row in chosen:
        identifier = "observation:" + row["observation_id"]
        nodes[identifier] = KnowledgeNode(
            node_id=identifier,
            kind="observation",
            label=row["text"][:500],
            evidence_ids=[identifier],
        )
        for key in row["service_keys"]:
            relationship: Literal["ABOUT_SERVICE", "FROM_SERVICE", "TO_SERVICE"] = "ABOUT_SERVICE"
            if row["observation_type"] == "dependency":
                relationship = "FROM_SERVICE" if key == row["caller_service_key"] else "TO_SERVICE"
            edges.append(
                KnowledgeEdge(
                    source=identifier,
                    target="service:" + key,
                    relationship=relationship,
                    evidence_ids=[identifier],
                )
            )
    chosen_chunks = sorted(chunks, key=lambda r: (r["source_id"], r["start_char"]))[:12]
    for row in chosen_chunks:
        identifier = "chunk:" + row["chunk_id"]
        document = "document:" + row["document_id"]
        if document not in nodes:
            nodes[document] = KnowledgeNode(
                node_id=document, kind="document", label=row["title"], evidence_ids=[]
            )
        nodes[document].evidence_ids.append(identifier)
        nodes[identifier] = KnowledgeNode(
            node_id=identifier, kind="chunk", label=row["text"][:500], evidence_ids=[identifier]
        )
        edges.append(
            KnowledgeEdge(
                source=document,
                target=identifier,
                relationship="HAS_CHUNK",
                evidence_ids=[identifier],
            )
        )
        for key in sorted(set(row["service_keys"]) & names.keys()):
            edges.append(
                KnowledgeEdge(
                    source=identifier,
                    target="service:" + key,
                    relationship="MENTIONS",
                    evidence_ids=[identifier],
                )
            )
    assertions_added: set[str] = set()
    for row in chosen_chunks:
        for assertion in row.get("verified_assertions", []):
            identifier = "assertion:" + assertion["assertion_id"]
            if identifier in assertions_added or len(assertions_added) >= 24:
                continue
            assertions_added.add(identifier)
            support = "chunk:" + row["chunk_id"]
            nodes[identifier] = KnowledgeNode(
                node_id=identifier,
                kind="assertion",
                label=(assertion["predicate"] + ": " + assertion["quote"])[:500],
                evidence_ids=[support],
            )
            edges.append(
                KnowledgeEdge(
                    source=identifier,
                    target=support,
                    relationship="SUPPORTED_BY",
                    evidence_ids=[support],
                )
            )
            key = assertion["service_key"]
            if key in names:
                edges.append(
                    KnowledgeEdge(
                        source=identifier,
                        target="service:" + key,
                        relationship="ABOUT_SERVICE",
                        evidence_ids=[support],
                    )
                )
    assertion_count = len(
        {a["assertion_id"] for c in chunks for a in c.get("verified_assertions", [])}
    )
    return KnowledgeResponse(
        episode_id=graph.episode_id,
        snapshot_id=graph.snapshot_id,
        as_of=graph.as_of,
        temporal_mode=graph.temporal_mode,
        nodes=list(nodes.values()),
        edges=edges[:256],
        omitted_observations=len(eligible) - len(chosen),
        omitted_chunks=len(chunks) - len(chosen_chunks),
        omitted_assertions=assertion_count - len(assertions_added),
        truncated=graph.truncated
        or len(eligible) > len(chosen)
        or len(chunks) > len(chosen_chunks)
        or len(edges) > 256
        or assertion_count > len(assertions_added),
    )


def reachable_checks(
    graph: GraphResponse,
    chunks: tuple[dict[str, Any], ...],
    symptom: str,
) -> KnowledgeChecksResponse:
    """Directed bounded path join: symptom -> calls -> service <- authored check.

    Hop distance orders relevance; it is not a causal or failure probability.
    Every input edge and assertion has already passed authoritative validation.
    """
    if symptom not in {node.service_id for node in graph.nodes}:
        raise ValueError("symptom service is not visible")
    paths: dict[str, list[GraphEdge]] = {symptom: []}
    pending = deque([symptom])
    while pending:
        current = pending.popleft()
        if len(paths[current]) >= 3:
            continue
        for edge in sorted(graph.edges, key=lambda e: (e.source_service_id, e.target_service_id)):
            if edge.source_service_id == current and edge.target_service_id not in paths:
                paths[edge.target_service_id] = [*paths[current], edge]
                pending.append(edge.target_service_id)
    found: dict[str, KnowledgeCheck] = {}
    for row in sorted(chunks, key=lambda r: (r["source_id"], r["start_char"])):
        for assertion in row.get("verified_assertions", []):
            target = assertion["service_key"]
            if assertion["predicate"] != "SUGGESTS_CHECK" or target not in paths:
                continue
            identifier = assertion["assertion_id"]
            if identifier in found:
                continue
            edges = paths[target]
            found[identifier] = KnowledgeCheck(
                assertion_id=identifier,
                service_id=target,
                quote=assertion["quote"],
                document_title=row["title"],
                evidence_id="chunk:" + row["chunk_id"],
                start_char=assertion["start_char"],
                end_char=assertion["end_char"],
                service_ids=[symptom, *(edge.target_service_id for edge in edges)],
                call_edges=edges,
            )
    checks = sorted(
        found.values(), key=lambda c: (len(c.call_edges), c.service_id, str(c.assertion_id))
    )
    return KnowledgeChecksResponse(
        episode_id=graph.episode_id,
        snapshot_id=graph.snapshot_id,
        as_of=graph.as_of,
        temporal_mode=graph.temporal_mode,
        symptom_service_id=symptom,
        checks=checks[:12],
        truncated=graph.truncated or len(checks) > 12,
    )
