"""Typed evidence conversion and deterministic template narrative."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from tracecause.contracts.models import (
    Claim,
    Evidence,
    EvidenceLocator,
    GraphEdge,
    GraphPath,
    Hypothesis,
    Metric,
    Narrative,
    RankedScore,
    Summary,
    TimeInterval,
)
from tracecause.graph.queries import GraphReader
from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.retrieval.engine import EvidenceCandidate

TEMPLATE_VERSION = "tracecause-template-v8"
TEMPLATE_PROMPT_HASH = hashlib.sha256(TEMPLATE_VERSION.encode()).hexdigest()


def ns_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value // 1_000_000_000, UTC).replace(
        microsecond=(value % 1_000_000_000) // 1000
    )


def evidence_from_candidate(
    candidate: EvidenceCandidate,
    reader: GraphReader,
    locators: SpanLocatorResolver,
    *,
    mode: Literal["event", "available"],
) -> Evidence:
    if candidate.kind == "chunk":
        locator = candidate.source_locator
        return Evidence(
            evidence_id=candidate.evidence_id,
            workspace_id=candidate.workspace_id,
            episode_id=candidate.episode_id,
            snapshot_id=candidate.snapshot_id,
            kind="document_chunk",
            text=candidate.text,
            interval=None,
            published_at=datetime.fromisoformat(locator["published_at"]),
            available_at=(
                datetime.fromisoformat(locator["available_at"])
                if locator.get("available_at")
                else None
            ),
            locator=EvidenceLocator(
                source_hash=locator["source_hash"],
                record_offset=None,
                start_char=locator["start_char"],
                end_char=locator["end_char"],
                trace_id=None,
                span_ids=[],
            ),
            metrics=[],
            quality_flags=[],
        )
    observation_id = UUID(candidate.source_locator["observation_id"])
    raw = reader.observation(
        workspace_id=candidate.workspace_id,
        episode_id=candidate.episode_id,
        snapshot_id=candidate.snapshot_id,
        as_of_ns=candidate.as_of_ns,
        observation_id=observation_id,
        mode=mode,
    )
    supports = raw["supports"]
    if not supports:
        raise ValueError("retrieved observation lacks a source locator")
    first = next((ref for ref in supports if ref["role"] == "measurement"), supports[0])
    same_trace = [
        first["span_id"],
        *[
            ref["span_id"]
            for ref in supports
            if ref["trace_id"] == first["trace_id"]
            and ref["artifact_hash"] == first["artifact_hash"]
            and ref["span_id"] != first["span_id"]
        ],
    ]
    interval = TimeInterval(start=ns_datetime(raw["start_ns"]), end=ns_datetime(raw["end_ns"]))
    metrics = [
        Metric(
            observation_id=str(observation_id),
            metric_name="request_count",
            value=float(raw["sample_count"]),
            unit="count",
            interval=interval,
        ),
        Metric(
            observation_id=str(observation_id),
            metric_name="error_count",
            value=float(raw["error_count"]),
            unit="count",
            interval=interval,
        ),
    ]
    for name in ("p50_ms", "p95_ms"):
        if raw[name] is not None:
            metrics.append(
                Metric(
                    observation_id=str(observation_id),
                    metric_name=name,
                    value=raw[name],
                    unit="ms",
                    interval=interval,
                )
            )
    return Evidence(
        evidence_id=candidate.evidence_id,
        workspace_id=candidate.workspace_id,
        episode_id=candidate.episode_id,
        snapshot_id=candidate.snapshot_id,
        kind="observation",
        text=candidate.text,
        interval=interval,
        published_at=None,
        available_at=(datetime.fromisoformat(raw["available_at"]) if raw["available_at"] else None),
        locator=EvidenceLocator(
            source_hash=first["artifact_hash"],
            record_offset=locators.offset(candidate, first, mode=mode),
            start_char=None,
            end_char=None,
            trace_id=first["trace_id"],
            span_ids=list(dict.fromkeys(same_trace))[:20],
        ),
        metrics=metrics,
        quality_flags=["support_locator_truncated"] if len(same_trace) > 20 else [],
    )


def path_from_reader(
    raw: dict[str, Any],
    *,
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
    as_of: datetime,
) -> GraphPath:
    services = list(raw["service_keys"])
    steps = list(raw["edges"])
    edges: list[GraphEdge] = []
    for item in steps:
        edge = dict(item)
        support = [f"observation:{value}" for value in edge["support_observation_ids"]]
        material = json.dumps(
            [edge["caller_service_key"], edge["callee_service_key"], support],
            sort_keys=True,
        ).encode()
        edges.append(
            GraphEdge(
                edge_id=f"edge:{hashlib.sha256(material).hexdigest()[:24]}",
                source_service_id=edge["caller_service_key"],
                target_service_id=edge["callee_service_key"],
                relationship="CALLS",
                supporting_evidence_ids=support[:1],
            )
        )
    path_material = json.dumps(
        [str(snapshot_id), services, [item.edge_id for item in edges]]
    ).encode()
    return GraphPath(
        path_id=f"path:{hashlib.sha256(path_material).hexdigest()[:24]}",
        workspace_id=workspace_id,
        episode_id=episode_id,
        snapshot_id=snapshot_id,
        as_of=as_of,
        service_ids=services,
        edges=edges,
    )


def template_narrative(
    status: Literal["supported_hypotheses", "insufficient_evidence", "no_anomaly_detected"],
    ranked_scores: list[RankedScore],
    support_by_service: dict[str, Evidence],
    conflicts_by_service: dict[str, list[str]],
    checks_by_service: dict[str, list[str]],
    paths_by_service: dict[str, GraphPath],
    limitations: list[str],
    baseline_by_service: dict[str, Evidence] | None = None,
) -> Narrative:
    if status == "no_anomaly_detected":
        cited = [item.evidence_id for item in support_by_service.values()][:1]
        return Narrative(
            status=status,
            summary=Summary(
                text="No qualifying anomaly was detected in the eligible analysis window.",
                evidence_ids=cited,
            ),
            hypotheses=[],
            claims=[],
            limitations=limitations,
        )
    if status == "insufficient_evidence":
        return Narrative(
            status=status,
            summary=Summary(
                text="The eligible evidence is insufficient for a supported service hypothesis.",
                evidence_ids=[],
            ),
            hypotheses=[],
            claims=[],
            limitations=limitations,
        )
    hypotheses: list[Hypothesis] = []
    claims: list[Claim] = []
    for item in ranked_scores:
        evidence = support_by_service[item.service_id]
        baseline = (baseline_by_service or {}).get(item.service_id)
        support_ids = [evidence.evidence_id] + ([baseline.evidence_id] if baseline else [])
        claim_ids = []
        hypothesis_claim_id = f"claim:{item.rank}:hypothesis"
        claims.append(
            Claim(
                claim_id=hypothesis_claim_id,
                claim_type="hypothesis",
                text=(
                    "Review the recorded analysis measurements and baseline example for this "
                    "service. One baseline bucket does not establish a window-wide change "
                    "or a cause."
                    if baseline
                    else "Review the recorded analysis measurements for this service; "
                    "these measurements alone do not establish a change or a cause."
                ),
                evidence_ids=support_ids,
                metric=None,
                path_id=None,
            )
        )
        claim_ids.append(hypothesis_claim_id)
        if evidence.metrics:
            numeric_id = f"claim:{item.rank}:request_count"
            claims.append(
                Claim(
                    claim_id=numeric_id,
                    claim_type="numeric",
                    text="Observed request volume is recorded in the attached metric.",
                    evidence_ids=[evidence.evidence_id],
                    metric=evidence.metrics[0],
                    path_id=None,
                )
            )
            claim_ids.append(numeric_id)
        path = paths_by_service.get(item.service_id)
        if path is not None:
            relationship_id = f"claim:{item.rank}:path"
            path_support = list(
                dict.fromkeys(
                    evidence_id
                    for edge in path.edges
                    for evidence_id in edge.supporting_evidence_ids
                )
            )
            claims.append(
                Claim(
                    claim_id=relationship_id,
                    claim_type="relationship",
                    text="An observed directed call path connects the symptom and this candidate.",
                    evidence_ids=path_support,
                    metric=None,
                    path_id=path.path_id,
                )
            )
            claim_ids.append(relationship_id)
        hypotheses.append(
            Hypothesis(
                service_id=item.service_id,
                rank=item.rank,
                claim_ids=claim_ids,
                supporting_evidence_ids=support_ids,
                conflicting_evidence_ids=conflicts_by_service.get(item.service_id, []),
                diagnostic_check_ids=checks_by_service.get(item.service_id, []),
            )
        )
    return Narrative(
        status=status,
        summary=Summary(
            text="Eligible observations identify service locations that warrant read-only checks.",
            evidence_ids=[item.supporting_evidence_ids[0] for item in hypotheses],
        ),
        hypotheses=hypotheses,
        claims=claims,
        limitations=limitations,
    )
