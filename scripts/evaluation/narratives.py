"""Shared template generation and source checks for offline benchmark systems."""

from __future__ import annotations

import time
from typing import Any

from tracecause.contracts.models import Evidence, RankedScore
from tracecause.documents.markdown import model_tokenizer
from tracecause.graph.queries import GraphReader
from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.investigation.templates import (
    evidence_from_candidate,
    ns_datetime,
    path_from_reader,
    template_narrative,
)
from tracecause.storage.artifacts import ArtifactRepository

from .resolve_review import resolve_candidate
from .systems import PublicEpisode


def generate(
    episode: PublicEpisode,
    prediction: dict[str, Any],
    reader: GraphReader,
    artifacts: ArtifactRepository,
    evidence_cache: dict[str, Evidence],
    span_cache: dict[str, dict[tuple[str, str], dict[str, Any]]],
) -> dict[str, Any]:
    """Use the production template with only the already budgeted context.

    Checks measure source/reference and numeric consistency, not semantic entailment.
    No source is added to improve an answer after observing a target label.
    """
    started = time.perf_counter()
    corpus = {item.evidence_id: item for item in episode.corpus}
    selected = {}
    locators = SpanLocatorResolver(artifacts)
    source_references = 0
    for key in prediction["context_ids"]:
        item = corpus[key]
        _, count = resolve_candidate(item, artifacts, span_cache)
        source_references += count
        if key not in evidence_cache:
            evidence_cache[key] = evidence_from_candidate(item, reader, locators, mode="event")
        selected[key] = evidence_cache[key]
    scores = [
        RankedScore(
            service_id=key,
            rank=index,
            score=prediction["candidate_scores"][key],
            score_type=(
                "linear_decision_score"
                if prediction["system"] == "graph_learned"
                else "heuristic_score"
            ),
        )
        for index, key in enumerate(prediction["exposed_ranking"], 1)
    ]
    signals = {item.service_key: item for item in episode.report.signals}
    support = {
        key: selected[f"observation:{signals[key].analysis_observation_id}"]
        for key in prediction["exposed_ranking"]
    }
    if prediction["status"] == "no_anomaly_detected":
        for evidence in selected.values():
            if evidence.kind == "observation":
                support["summary"] = evidence
                break
    checks = {
        key: [
            evidence_id
            for evidence_id, evidence in selected.items()
            if evidence.kind == "document_chunk"
            and "**Check:**" in evidence.text
            and key in corpus[evidence_id].service_keys
        ]
        for key in prediction["exposed_ranking"]
    }
    paths = {}
    symptom = episode.names.get("storefront")
    if prediction["system"].startswith("graph") and symptom:
        for key in prediction["exposed_ranking"]:
            if key == symptom:
                continue
            for raw in reader.support_paths(
                workspace_id=episode.workspace_id,
                episode_id=episode.episode_id,
                snapshot_id=episode.snapshot_id,
                as_of_ns=episode.cutoff,
                source_service_key=symptom,
                target_service_key=key,
            ):
                path = path_from_reader(
                    raw,
                    workspace_id=episode.workspace_id,
                    episode_id=episode.episode_id,
                    snapshot_id=episode.snapshot_id,
                    as_of=ns_datetime(episode.cutoff),
                )
                if all(
                    evidence_id in selected
                    for edge in path.edges
                    for evidence_id in edge.supporting_evidence_ids
                ):
                    paths[key] = path
                    break
    narrative = template_narrative(
        prediction["status"],
        scores,
        support,
        {},
        checks,
        paths,
        [
            "Controlled demonstration data; observed calls do not establish causality.",
            "Template generation; no local language-model inference in this benchmark.",
        ],
    )
    cited = set(narrative.summary.evidence_ids)
    numeric = 0
    for claim in narrative.claims:
        cited.update(claim.evidence_ids)
        if not set(claim.evidence_ids) <= selected.keys():
            raise ValueError("template claim cites evidence outside shared context")
        if claim.claim_type == "numeric":
            if claim.metric is None or not any(
                claim.metric in selected[key].metrics for key in claim.evidence_ids
            ):
                raise ValueError("template numeric claim differs from database evidence")
            numeric += 1
    for hypothesis in narrative.hypotheses:
        cited.update(hypothesis.supporting_evidence_ids)
        cited.update(hypothesis.diagnostic_check_ids)
    if not cited <= selected.keys():
        raise ValueError("template reference outside context")
    if [item.service_id for item in narrative.hypotheses] != prediction["exposed_ranking"]:
        raise ValueError("template changed deterministic ranking")
    tokenizer = model_tokenizer()
    return {
        "narrative": narrative.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in selected.values()],
        "paths": [item.model_dump(mode="json") for item in paths.values()],
        "generation_origin": "template",
        "source_resolved_context_items": len(selected),
        "source_resolved_span_or_document_references": source_references,
        "valid_unique_citations": len(cited),
        "unique_citations": len(cited),
        "consistent_numeric_claims": numeric,
        "numeric_claims": numeric,
        "semantic_claim_support": "requires_ai_review",
        "output_tokens_minilm": len(
            tokenizer.encode(narrative.model_dump_json(), add_special_tokens=False).ids
        ),
        "generation_and_source_validation_ms": (time.perf_counter() - started) * 1000,
    }
