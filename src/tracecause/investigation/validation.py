"""Revalidate citations, metrics, paths and fixed ranks before publication."""

from __future__ import annotations

import calendar
import math
import re
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from tracecause.contracts.models import (
    FeatureContribution,
    InvestigationResult,
    Narrative,
    RankingMode,
    TemporalMode,
)
from tracecause.graph.queries import GraphReader
from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.investigation.planning import ExplanationPlan, render_plan
from tracecause.investigation.ranking import CandidateScore
from tracecause.retrieval.engine import RetrievalRequest, corpus_from_reader
from tracecause.storage.artifacts import ArtifactRepository

MAX_EVIDENCE_TOKENS = 6000


class InvestigationValidationError(ValueError):
    """A proposed report is not supported by authoritative eligible evidence."""


UNSAFE_PROSE = re.compile(
    r"\b(?:sudo|kubectl|curl|rm\s+-rf|execute|executed|confirmed\s+root\s+cause|"
    r"root\s+cause\s+is|definitely\s+caused)\b|`|\b\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)


def validate_generated_narrative(
    generated: Narrative,
    template: Narrative,
    plan: ExplanationPlan | None = None,
    *,
    question: str = "",
) -> None:
    """Allow prose changes only; all factual references stay server-owned."""
    if plan is not None:
        # Recompile independently: no free-form model prose or changed support is accepted.
        try:
            compiled = render_plan(plan, template, question)
        except ValueError as exc:
            raise InvestigationValidationError("generated evidence plan is invalid") from exc
        if generated != compiled:
            raise InvestigationValidationError("generated narrative differs from compiled plan")
        return
    if (
        generated.status != template.status
        or generated.summary.evidence_ids != template.summary.evidence_ids
        or generated.hypotheses != template.hypotheses
        or generated.limitations != template.limitations
        or len(generated.claims) != len(template.claims)
    ):
        raise InvestigationValidationError("generated narrative changed fixed support")
    for actual, expected in zip(generated.claims, template.claims, strict=True):
        if actual.model_dump(exclude={"text"}) != expected.model_dump(exclude={"text"}):
            raise InvestigationValidationError("generated claim changed typed support")
    for value in (
        generated.summary.text,
        *(claim.text for claim in generated.claims),
    ):
        if UNSAFE_PROSE.search(value):
            raise InvestigationValidationError("generated prose contains unsupported content")


def as_ns(value: object) -> int:
    from datetime import datetime

    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvestigationValidationError("cutoff must be timezone-aware")
    return calendar.timegm(value.utctimetuple()) * 1_000_000_000 + value.microsecond * 1000


def validate_result(
    result: InvestigationResult,
    reader: GraphReader,
    artifacts: ArtifactRepository,
    expected_scores: tuple[CandidateScore, ...],
    expected_explanations: Mapping[str, tuple[dict[str, Any], ...]] | None = None,
) -> None:
    """Fail closed on stale scope, invented sources or altered numeric/rank claims."""
    cutoff = as_ns(result.as_of)
    mode: Literal["event", "available"] = (
        "available" if result.temporal_mode is TemporalMode.AVAILABLE else "event"
    )
    request = RetrievalRequest(
        workspace_id=result.workspace_id,
        episode_id=result.episode_id,
        snapshot_id=result.snapshot_id,
        as_of_ns=cutoff,
        mode=mode,
        question="citation validation",
    )
    eligible = {item.evidence_id: item for item in corpus_from_reader(reader, artifacts, request)}
    locators = SpanLocatorResolver(artifacts)
    for item in result.evidence:
        source = eligible.get(item.evidence_id)
        if source is None or item.text != source.text:
            raise InvestigationValidationError("citation is absent or text differs from source")
        if item.kind == "document_chunk":
            if (
                source.kind != "chunk"
                or item.locator.source_hash != source.source_locator["source_hash"]
                or item.locator.start_char != source.source_locator["start_char"]
                or item.locator.end_char != source.source_locator["end_char"]
                or item.published_at is None
                or item.published_at.isoformat() != source.source_locator["published_at"]
                or (item.available_at.isoformat() if item.available_at else None)
                != source.source_locator["available_at"]
                or item.interval is not None
                or item.metrics
                or item.locator.record_offset is not None
                or item.locator.trace_id is not None
            ):
                raise InvestigationValidationError("document citation metadata differs")
        elif item.kind == "observation":
            if source.kind != "observation" or item.interval is None:
                raise InvestigationValidationError("observation citation is malformed")
            observation_id = UUID(source.source_locator["observation_id"])
            raw = reader.observation(
                workspace_id=result.workspace_id,
                episode_id=result.episode_id,
                snapshot_id=result.snapshot_id,
                as_of_ns=cutoff,
                observation_id=observation_id,
                mode=mode,
            )
            if (
                as_ns(item.interval.start) != raw["start_ns"]
                or as_ns(item.interval.end) != raw["end_ns"]
                or raw["provenance_hash"] != source.source_locator["provenance_hash"]
                or (item.available_at.isoformat() if item.available_at else None)
                != raw["available_at"]
                or item.locator.start_char is not None
                or item.locator.end_char is not None
                or not any(
                    ref["artifact_hash"] == item.locator.source_hash
                    and item.locator.span_ids
                    and ref["span_id"] == item.locator.span_ids[0]
                    and locators.offset(source, ref, mode=mode) == item.locator.record_offset
                    and ref["trace_id"] == item.locator.trace_id
                    for ref in raw["supports"]
                )
            ):
                raise InvestigationValidationError("observation locator or interval differs")
            if not set(item.locator.span_ids) <= {
                ref["span_id"]
                for ref in raw["supports"]
                if ref["trace_id"] == item.locator.trace_id
                and ref["artifact_hash"] == item.locator.source_hash
            }:
                raise InvestigationValidationError("observation span references differ")
            for metric in item.metrics:
                if metric.observation_id != str(observation_id) or metric.interval != item.interval:
                    raise InvestigationValidationError("metric observation or window differs")
                expected_value: float | None
                if metric.metric_name == "request_count" and metric.unit == "count":
                    expected_value = float(raw["sample_count"])
                elif metric.metric_name == "error_count" and metric.unit == "count":
                    expected_value = float(raw["error_count"])
                elif metric.metric_name == "p50_ms" and metric.unit == "ms":
                    expected_value = raw["p50_ms"]
                elif metric.metric_name == "p95_ms" and metric.unit == "ms":
                    expected_value = raw["p95_ms"]
                else:
                    raise InvestigationValidationError("metric type or unit is unsupported")
                if expected_value is None or not math.isclose(
                    metric.value, expected_value, abs_tol=1e-9, rel_tol=0
                ):
                    raise InvestigationValidationError("numeric metric differs from source")
        else:
            raise InvestigationValidationError("unsupported evidence kind")
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    paths_by_id = {item.path_id: item for item in result.paths}
    for claim in result.narrative.claims:
        if claim.claim_type == "numeric" and (
            claim.metric is None
            or not any(
                claim.metric in evidence_by_id[evidence_id].metrics
                for evidence_id in claim.evidence_ids
            )
        ):
            raise InvestigationValidationError("numeric claim lacks an exact evidence metric")
        if claim.claim_type == "relationship":
            path = paths_by_id.get(claim.path_id) if claim.path_id is not None else None
            if path is None or not {
                evidence_id for edge in path.edges for evidence_id in edge.supporting_evidence_ids
            } <= set(claim.evidence_ids):
                raise InvestigationValidationError("relationship claim lacks path support")
    calls = reader.eligible_calls(
        workspace_id=result.workspace_id,
        episode_id=result.episode_id,
        snapshot_id=result.snapshot_id,
        as_of_ns=cutoff,
        mode=mode,
    )
    calls_by_pair = {(item.caller_service_key, item.callee_service_key): item for item in calls}
    for path in result.paths:
        for edge in path.edges:
            call = calls_by_pair.get((edge.source_service_id, edge.target_service_id))
            if call is None or any(
                evidence_id not in evidence_by_id
                or evidence_id
                not in {f"observation:{value}" for value in call.support_observation_ids}
                for evidence_id in edge.supporting_evidence_ids
            ):
                raise InvestigationValidationError("path direction or support differs")
    expected = expected_scores[: len(result.ranked_scores)]
    if len(expected) != len(result.ranked_scores):
        raise InvestigationValidationError("ranked score count differs")
    for rank, (actual, reference) in enumerate(
        zip(result.ranked_scores, expected, strict=True), start=1
    ):
        if (
            actual.rank != rank
            or actual.service_id != reference.service_key
            or actual.score_type
            != (
                "linear_decision_score"
                if result.ranking_mode is RankingMode.LEARNED
                else "heuristic_score"
            )
            or not math.isclose(actual.score, reference.score, abs_tol=1e-12, rel_tol=0)
        ):
            raise InvestigationValidationError("candidate rank or score differs")
        if result.ranking_mode is RankingMode.LEARNED:
            if expected_explanations is None or [
                item.model_dump(mode="json") for item in actual.feature_contributions
            ] != [
                FeatureContribution.model_validate(item).model_dump(mode="json")
                for item in expected_explanations.get(actual.service_id, ())
            ]:
                raise InvestigationValidationError("ranker feature contribution differs")
            if not actual.feature_contributions or any(
                any(
                    evidence_id not in eligible or eligible[evidence_id].kind != "observation"
                    for evidence_id in contribution.evidence_ids
                )
                or not contribution.evidence_ids
                for contribution in actual.feature_contributions
            ):
                raise InvestigationValidationError("ranker explanation lacks eligible observation")
        elif actual.feature_contributions:
            raise InvestigationValidationError("heuristic score has learned contributions")
    for hypothesis in result.narrative.hypotheses:
        if (
            hypothesis.rank > len(result.ranked_scores)
            or result.ranked_scores[hypothesis.rank - 1].service_id != hypothesis.service_id
            or not all(
                hypothesis.service_id in eligible[evidence_id].service_keys
                for evidence_id in hypothesis.supporting_evidence_ids
            )
        ):
            raise InvestigationValidationError("hypothesis lacks matching ranked support")
        for check_id in hypothesis.diagnostic_check_ids:
            evidence = evidence_by_id.get(check_id)
            if (
                evidence is None
                or evidence.kind != "document_chunk"
                or "**Check:**" not in evidence.text
            ):
                raise InvestigationValidationError("diagnostic check is not an eligible runbook")
    from tracecause.documents.markdown import model_tokenizer

    tokenizer = model_tokenizer()
    token_count = sum(len(tokenizer.encode(item.text).ids) for item in result.evidence)
    if token_count > MAX_EVIDENCE_TOKENS:
        raise InvestigationValidationError("evidence exceeds context budget")
