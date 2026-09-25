"""Deterministic, source-validated investigation and immutable result persistence."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from neo4j import Driver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.canonical import canonical_json, semantic_sha256
from tracecause.contracts.models import (
    FeatureContribution,
    GenerationMode,
    GenerationOrigin,
    InvestigationRequest,
    InvestigationResult,
    RankedScore,
    RankingMode,
    ResultProvenance,
    RetrievalMode,
    TemporalMode,
    Usage,
)
from tracecause.graph.queries import GraphReader
from tracecause.investigation.generation import (
    PINNED_MANIFEST_DIGEST,
    GenerationError,
    GenerationProvider,
    generation_cache_hash,
    safe_bundle,
)
from tracecause.investigation.locators import SpanLocatorResolver
from tracecause.investigation.ranking import (
    HEURISTIC_VERSION,
    generate_candidates,
    is_anomalous,
    score_candidates,
)
from tracecause.investigation.templates import (
    TEMPLATE_PROMPT_HASH,
    TEMPLATE_VERSION,
    evidence_from_candidate,
    path_from_reader,
    template_narrative,
)
from tracecause.investigation.validation import (
    InvestigationValidationError,
    as_ns,
    validate_generated_narrative,
    validate_result,
)
from tracecause.ranking.features import candidate_features
from tracecause.ranking.model import SUPPORTED_MODEL_VERSIONS, LinearRanker, ModelCompatibilityError
from tracecause.retrieval.embeddings import MODEL_REVISION, Embedder, MiniLMEmbedder
from tracecause.retrieval.engine import (
    EvidenceCandidate,
    RetrievalRequest,
    Strategy,
    corpus_from_reader,
    retrieve,
)
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import StorageConflict, register_artifact
from tracecause.storage.schema import (
    EpisodeRow,
    InvestigationRow,
    ModelRegistryRow,
    SnapshotRow,
    SourceArtifactRow,
)
from tracecause.telemetry.persistence import compute_persisted_signals
from tracecause.telemetry.signals import SignalFeature

INVESTIGATION_NAMESPACE = UUID("d71d1b56-332d-4f5e-aa80-a266a03f06d5")
MAX_FINAL_EVIDENCE = 12
MAX_HYPOTHESES = 3


def _strategy(mode: RetrievalMode) -> Strategy:
    strategies: dict[RetrievalMode, Strategy] = {
        RetrievalMode.DENSE: "dense",
        RetrievalMode.LEXICAL: "lexical",
        RetrievalMode.HYBRID: "hybrid",
        RetrievalMode.GRAPH: "graph",
    }
    return strategies[mode]


def _mode(value: TemporalMode) -> Literal["event", "available"]:
    return "available" if value is TemporalMode.AVAILABLE else "event"


def _support_for_service(
    key: str,
    signal: SignalFeature,
    corpus: dict[str, EvidenceCandidate],
) -> EvidenceCandidate | None:
    preferred = corpus.get(f"observation:{signal.analysis_observation_id}")
    if preferred is not None and preferred.service_keys == (key,):
        return preferred
    alternatives = [
        item
        for item in corpus.values()
        if item.kind == "observation"
        and item.service_keys == (key,)
        and item.source_locator["start_ns"] >= signal.analysis_start_ns
        and item.source_locator["end_ns"] <= signal.analysis_end_ns
    ]
    return max(
        alternatives,
        key=lambda item: (item.source_locator["end_ns"], item.evidence_id),
        default=None,
    )


class InvestigationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        driver: Driver | None,
        artifacts: ArtifactRepository,
        embedding_cache_root: Path,
        embedder: Embedder | None = None,
        generation_provider: GenerationProvider | None = None,
        generation_settings: RuntimeSettings | None = None,
        generation_prompt_hash: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.reader = GraphReader(session_factory, driver) if driver is not None else None
        self.artifacts = artifacts
        self.embedding_cache_root = embedding_cache_root
        self.embedder = embedder
        self.generation_provider = generation_provider
        self.generation_settings = generation_settings
        self.generation_prompt_hash = generation_prompt_hash

    def _scope(
        self, workspace_id: UUID, request: InvestigationRequest
    ) -> tuple[EpisodeRow, SnapshotRow]:
        with self.session_factory() as session:
            episode = session.get(EpisodeRow, request.episode_id)
            snapshot_id = request.snapshot_id or (
                episode.active_snapshot_id if episode is not None else None
            )
            snapshot = session.get(SnapshotRow, snapshot_id) if snapshot_id is not None else None
            if (
                episode is None
                or snapshot is None
                or episode.workspace_id != workspace_id
                or snapshot.workspace_id != workspace_id
                or snapshot.episode_id != episode.id
                or snapshot.status != "READY"
                or episode.ingestion_status != "IMPORTED"
            ):
                raise StorageConflict("investigation snapshot is unpublished or outside scope")
            return episode, snapshot

    def _promoted_ranker(self, workspace_id: UUID) -> tuple[LinearRanker | None, str | None]:
        with self.session_factory() as session:
            registrations = session.scalars(
                select(ModelRegistryRow)
                .join(
                    SourceArtifactRow,
                    SourceArtifactRow.content_hash == ModelRegistryRow.artifact_hash,
                )
                .where(ModelRegistryRow.model_name.in_(SUPPORTED_MODEL_VERSIONS))
                .where(SourceArtifactRow.workspace_id == workspace_id)
                .where(ModelRegistryRow.promoted_at.is_not(None))
                .order_by(ModelRegistryRow.promoted_at.desc())
                .limit(1)
            ).all()
        if not registrations:
            return None, "learned_ranker_model_missing"
        registration = registrations[0]
        try:
            raw = self.artifacts.get_bytes(registration.artifact_hash)
            model = LinearRanker.from_bytes(raw, expected_hash=registration.artifact_hash)
            if (
                registration.feature_schema_version != model.artifact["feature_schema_version"]
                or model.artifact["validation"]["selected_default"] != "learned"
            ):
                raise ModelCompatibilityError("ranker registration incompatible")
            return model, None
        except (ModelCompatibilityError, KeyError, ValueError):
            return None, "learned_ranker_model_invalid"

    def investigate(
        self,
        workspace_id: UUID,
        request: InvestigationRequest,
        *,
        publication_guard: Callable[[Session, UUID], None] | None = None,
    ) -> InvestigationResult:
        reader = self.reader
        if reader is None:
            raise ValueError("graph reader is required to investigate")
        if request.generation_mode not in {GenerationMode.TEMPLATE, GenerationMode.OLLAMA}:
            raise ValueError("unsupported generation mode")
        if request.ranking_mode not in {RankingMode.HEURISTIC, RankingMode.LEARNED}:
            raise ValueError("unsupported ranking mode")
        started = time.perf_counter()
        episode, snapshot = self._scope(workspace_id, request)
        ranker: LinearRanker | None = None
        ranking_fallback: str | None = None
        if request.ranking_mode is RankingMode.LEARNED:
            ranker, ranking_fallback = self._promoted_ranker(workspace_id)
        actual_ranking_mode = RankingMode.LEARNED if ranker is not None else RankingMode.HEURISTIC
        as_of = request.as_of.astimezone(UTC)
        cutoff = as_ns(as_of)
        mode = _mode(request.temporal_mode)
        signals_started = time.perf_counter()
        report = compute_persisted_signals(
            self.session_factory,
            workspace_id=workspace_id,
            episode_id=episode.id,
            as_of_ns=cutoff,
            mode=mode,
        )
        signals_ms = (time.perf_counter() - signals_started) * 1000
        request_hash = semantic_sha256(
            {
                "request": request.model_dump(mode="json"),
                "snapshot_id": str(snapshot.id),
                "snapshot_hash": snapshot.content_hash,
                "signal_input_hash": report.input_hash,
                "signal_rule_version": report.rule_version,
                "template_version": TEMPLATE_VERSION,
                "heuristic_version": HEURISTIC_VERSION,
                "ranker_artifact_hash": ranker.artifact_hash if ranker is not None else None,
                "ranking_fallback": ranking_fallback,
                "embedding_revision": MODEL_REVISION,
            }
        )
        investigation_id = uuid5(INVESTIGATION_NAMESPACE, request_hash)
        if request.generation_mode is GenerationMode.TEMPLATE:
            with self.session_factory() as session:
                previous = session.get(InvestigationRow, investigation_id)
                if previous is not None:
                    if (
                        previous.workspace_id != workspace_id
                        or previous.episode_id != episode.id
                        or previous.snapshot_id != snapshot.id
                        or previous.request_hash != request_hash
                        or previous.result_artifact_hash is None
                    ):
                        raise StorageConflict("investigation identity conflicts")
                    saved = self.artifacts.get_bytes(previous.result_artifact_hash)
                    result = InvestigationResult.model_validate_json(saved)
                    if publication_guard is not None:
                        with self.session_factory.begin() as publication_session:
                            publication_guard(publication_session, investigation_id)
                    return result.model_copy(update={"is_replayed": True})
        retrieval_request = RetrievalRequest(
            workspace_id=workspace_id,
            episode_id=episode.id,
            snapshot_id=snapshot.id,
            as_of_ns=cutoff,
            mode=mode,
            question=request.question,
            symptom_service_key=request.symptom_service_id,
            top_k=40,
        )
        retrieval_started = time.perf_counter()
        embedder = self.embedder
        if request.retrieval_mode is not RetrievalMode.LEXICAL and embedder is None:
            embedder = MiniLMEmbedder()
        retrieval = retrieve(
            reader,
            self.artifacts,
            retrieval_request,
            strategy=_strategy(request.retrieval_mode),
            embedder=embedder,
            embedding_cache_root=self.embedding_cache_root,
        )
        corpus = {item.evidence_id: item for item in retrieval.corpus}
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        assembly_started = time.perf_counter()
        calls, call_truncated = reader.eligible_calls_bounded(
            workspace_id=workspace_id,
            episode_id=episode.id,
            snapshot_id=snapshot.id,
            as_of_ns=cutoff,
            mode=mode,
        )
        keys, candidate_truncated = generate_candidates(
            report.signals, calls, request.symptom_service_id
        )
        scores = score_candidates(report.signals, calls, keys, request.symptom_service_id)
        explanations_by_key: dict[str, tuple[dict[str, Any], ...]] = {}
        if ranker is not None:
            features = candidate_features(
                report,
                calls,
                keys,
                request.symptom_service_id,
                request.question,
                version=ranker.artifact["feature_schema_version"],
            )
            feature_by_key = {
                item.service_key: replace(
                    item,
                    evidence_ids=tuple(
                        identifier for identifier in item.evidence_ids if identifier in corpus
                    ),
                )
                for item in features
            }
            try:
                learned_scores: dict[str, float] = {}
                for score in scores:
                    if not feature_by_key[score.service_key].evidence_ids:
                        raise ModelCompatibilityError("ranker source observation unpublished")
                    if len(feature_by_key[score.service_key].evidence_ids) > 40:
                        raise ModelCompatibilityError("ranker explanation exceeds evidence bound")
                    learned_scores[score.service_key], explanations_by_key[score.service_key] = (
                        ranker.score(feature_by_key[score.service_key])
                    )
                scores = tuple(
                    sorted(
                        (
                            replace(score, score=learned_scores[score.service_key])
                            for score in scores
                        ),
                        key=lambda score: (-score.score, score.service_key),
                    )
                )
            except (ModelCompatibilityError, KeyError):
                ranking_fallback = "learned_ranker_feature_invalid"
                actual_ranking_mode = RankingMode.HEURISTIC
                scores = score_candidates(report.signals, calls, keys, request.symptom_service_id)
                explanations_by_key = {}
        signal_by_key = {item.service_key: item for item in report.signals}
        # Candidate exploration includes neighbors; exposed hypotheses require their
        # own adequate anomalous measurements. The frozen scorer is unchanged.
        scores = tuple(
            score
            for score in scores
            if is_anomalous(signal_by_key[score.service_key])
            and not {"insufficient_baseline", "no_analysis_samples"}.intersection(
                signal_by_key[score.service_key].flags
            )
        )
        warning_set = set(retrieval.warnings)
        if ranking_fallback is not None:
            warning_set.add(ranking_fallback)
        if call_truncated or candidate_truncated:
            warning_set.add("candidate_or_graph_truncated")
        unknown_symptom = (
            request.symptom_service_id is not None
            and request.symptom_service_id not in signal_by_key
        )
        if unknown_symptom:
            warning_set.add("unknown_symptom_service")
        adequate = cutoff > (episode.analysis_start_ns or 0) and any(
            "insufficient_baseline" not in item.flags and "no_analysis_samples" not in item.flags
            for item in report.signals
        )
        has_anomaly = any(is_anomalous(item) for item in report.signals)
        status: Literal["supported_hypotheses", "insufficient_evidence", "no_anomaly_detected"] = (
            "insufficient_evidence"
            if unknown_symptom or not adequate
            else "no_anomaly_detected"
            if not has_anomaly
            else "supported_hypotheses"
        )
        ranker_rejected = False
        if status == "supported_hypotheses" and actual_ranking_mode is RankingMode.LEARNED:
            assert ranker is not None
            threshold = ranker.rejection_threshold(episode.origin)
            if not scores or threshold is not None and scores[0].score < threshold:
                status = "insufficient_evidence"
                ranker_rejected = True
                warning_set.add("learned_ranker_below_development_threshold")
        if not adequate:
            warning_set.add("insufficient_baseline_or_samples")
        locators = SpanLocatorResolver(self.artifacts)
        selected: dict[str, Any] = {}
        support_by_service: dict[str, Any] = {}
        baseline_by_service: dict[str, Any] = {}
        ranked_scores: list[RankedScore] = []
        paths_by_service: dict[str, Any] = {}
        checks_by_service: dict[str, list[str]] = {}
        conflicts_by_service: dict[str, list[str]] = {}
        if status == "supported_hypotheses":
            if not scores:
                status = "insufficient_evidence"
                warning_set.add("no_adequate_anomalous_candidate")
        if status == "supported_hypotheses":
            output_cap = MAX_HYPOTHESES
            for candidate in scores[:output_cap]:
                signal = signal_by_key[candidate.service_key]
                source = _support_for_service(candidate.service_key, signal, corpus)
                if source is None or len(selected) >= MAX_FINAL_EVIDENCE:
                    status = "insufficient_evidence"
                    warning_set.add("candidate_support_unavailable_in_snapshot")
                    break
                evidence = evidence_from_candidate(source, reader, locators, mode=mode)
                selected[evidence.evidence_id] = evidence
                support_by_service[candidate.service_key] = evidence
                ranked_scores.append(
                    RankedScore(
                        service_id=candidate.service_key,
                        rank=len(ranked_scores) + 1,
                        score=candidate.score,
                        score_type="linear_decision_score"
                        if actual_ranking_mode is RankingMode.LEARNED
                        else "heuristic_score",
                        feature_contributions=[
                            FeatureContribution.model_validate(item)
                            for item in explanations_by_key.get(candidate.service_key, ())
                        ],
                    )
                )
            if status == "insufficient_evidence":
                ranked_scores = []
                support_by_service = {}
                selected = {}
        if status == "supported_hypotheses":
            for item in ranked_scores:
                key = item.service_id
                # A chronological middle bucket is explicitly an example, never a
                # surrogate for the full baseline distribution or its MAD.
                baseline_sources = sorted(
                    (
                        corpus[f"observation:{identifier}"]
                        for identifier in signal_by_key[key].baseline_observation_ids
                        if f"observation:{identifier}" in corpus
                        and corpus[f"observation:{identifier}"].service_keys == (key,)
                    ),
                    key=lambda source: (source.source_locator["end_ns"], source.evidence_id),
                )
                if baseline_sources and len(selected) < MAX_FINAL_EVIDENCE:
                    baseline = evidence_from_candidate(
                        baseline_sources[len(baseline_sources) // 2], reader, locators, mode=mode
                    )
                    selected[baseline.evidence_id] = baseline
                    baseline_by_service[key] = baseline
                if request.symptom_service_id and key != request.symptom_service_id:
                    raw_paths = reader.support_paths(
                        workspace_id=workspace_id,
                        episode_id=episode.id,
                        snapshot_id=snapshot.id,
                        as_of_ns=cutoff,
                        source_service_key=request.symptom_service_id,
                        target_service_key=key,
                        mode=mode,
                    )
                    if raw_paths and len(paths_by_service) < 3:
                        path = path_from_reader(
                            raw_paths[0],
                            workspace_id=workspace_id,
                            episode_id=episode.id,
                            snapshot_id=snapshot.id,
                            as_of=as_of,
                        )
                        missing = [
                            evidence_id
                            for edge in path.edges
                            for evidence_id in edge.supporting_evidence_ids
                            if evidence_id not in selected
                        ]
                        if len(selected) + len(set(missing)) <= MAX_FINAL_EVIDENCE and all(
                            evidence_id in corpus for evidence_id in missing
                        ):
                            for evidence_id in missing:
                                selected[evidence_id] = evidence_from_candidate(
                                    corpus[evidence_id], reader, locators, mode=mode
                                )
                            paths_by_service[key] = path
                checks = [
                    candidate
                    for candidate in retrieval.candidates
                    if candidate.kind == "chunk"
                    and key in candidate.service_keys
                    and any(
                        assertion["predicate"] == "SUGGESTS_CHECK"
                        and assertion["service_key"] == key
                        for assertion in candidate.source_locator.get("verified_assertions", [])
                    )
                ]
                if checks and len(selected) < MAX_FINAL_EVIDENCE:
                    check = evidence_from_candidate(checks[0], reader, locators, mode=mode)
                    selected[check.evidence_id] = check
                    checks_by_service[key] = [check.evidence_id]
                feature = signal_by_key[key]
                analysis = next(
                    (
                        obs
                        for obs in report.observations
                        if obs.id == feature.analysis_observation_id
                    ),
                    None,
                )
                if analysis is not None and analysis.p95_ms is not None:
                    conflicting = sorted(
                        (
                            obs
                            for obs in report.observations
                            if obs.observation_type == "service"
                            and obs.service_key == key
                            and obs.end_ns > feature.analysis_start_ns
                            and obs.attributes.get("bucket") is True
                            and obs.sample_count >= 10
                            and obs.error_count == 0
                            and obs.p95_ms is not None
                            and obs.p95_ms < analysis.p95_ms / 2
                        ),
                        key=lambda obs: (-obs.end_ns, str(obs.id)),
                    )
                    if conflicting and len(selected) < MAX_FINAL_EVIDENCE:
                        evidence_id = f"observation:{conflicting[0].id}"
                        if evidence_id in corpus:
                            conflict = evidence_from_candidate(
                                corpus[evidence_id], reader, locators, mode=mode
                            )
                            selected[evidence_id] = conflict
                            conflicts_by_service[key] = [evidence_id]
        if status == "no_anomaly_detected":
            suitable = sorted(
                (
                    item
                    for item in corpus.values()
                    if item.kind == "observation" and len(item.service_keys) == 1
                ),
                key=lambda item: (-item.source_locator["end_ns"], item.evidence_id),
            )
            if suitable:
                evidence = evidence_from_candidate(suitable[0], reader, locators, mode=mode)
                selected[evidence.evidence_id] = evidence
                support_by_service[suitable[0].service_keys[0]] = evidence
        limitations = ["Observed CALLS edges describe communication, not a proven cause."]
        if ranker_rejected:
            limitations.append(
                "The top learned ranking score did not meet the development-set "
                "rejection threshold."
            )
        if "no_eligible_documents" in warning_set:
            limitations.append("No published runbook chunk was eligible at this cutoff.")
        if status == "insufficient_evidence":
            limitations.append(
                "Baseline, analysis samples or direct citation support was insufficient."
            )
        if call_truncated or candidate_truncated:
            limitations.append("The bounded graph or candidate set was truncated.")
        if (
            request.generation_mode is GenerationMode.OLLAMA
            and status == "supported_hypotheses"
            and len(scores) > 1
        ):
            warning_set.add("model_output_budget_selected_top_one")
            limitations.append(
                "The local model output budget permits only the highest-ranked candidate "
                "in this report."
            )
        narrative = template_narrative(
            status,
            ranked_scores,
            support_by_service,
            conflicts_by_service,
            checks_by_service,
            paths_by_service,
            limitations,
            baseline_by_service,
        )
        assembly_ms = (time.perf_counter() - assembly_started) * 1000
        generation_origin = GenerationOrigin.TEMPLATE
        fallback_reason: str | None = None
        prompt_hash = TEMPLATE_PROMPT_HASH
        model_digest: str | None = None
        prompt_tokens: int | None = None
        output_tokens: int | None = None
        generation_ms = 0.0
        if request.generation_mode is GenerationMode.OLLAMA:
            bundle = safe_bundle(
                question=request.question,
                status=status,
                cutoff=as_of.isoformat(),
                temporal_mode=request.temporal_mode.value,
                ranked_scores=[item.model_dump(mode="json") for item in ranked_scores],
                evidence=[item.model_dump(mode="json") for item in selected.values()],
                paths=[item.model_dump(mode="json") for item in paths_by_service.values()],
                allowed_checks=sorted(
                    {check for checks in checks_by_service.values() for check in checks}
                ),
                limitations=limitations,
                draft_narrative=narrative.model_dump(mode="json"),
            )
            provider = self.generation_provider
            settings = self.generation_settings
            provider_prompt_hash = self.generation_prompt_hash or TEMPLATE_PROMPT_HASH
            cache_hash = (
                generation_cache_hash(
                    bundle=bundle,
                    model_digest=PINNED_MANIFEST_DIGEST,
                    settings=settings,
                    prompt_hash=provider_prompt_hash,
                )
                if settings is not None
                else semantic_sha256({"bundle": bundle, "provider": "unconfigured"})
            )
            model_request_hash = semantic_sha256(
                {"request_hash": request_hash, "generation_cache": cache_hash, "origin": "ollama"}
            )
            model_id = uuid5(INVESTIGATION_NAMESPACE, model_request_hash)
            if provider is not None and settings is not None:
                with self.session_factory() as session:
                    prior = session.get(InvestigationRow, model_id)
                    if prior is not None:
                        if (
                            prior.workspace_id != workspace_id
                            or prior.episode_id != episode.id
                            or prior.snapshot_id != snapshot.id
                            or prior.request_hash != model_request_hash
                            or prior.result_artifact_hash is None
                        ):
                            raise StorageConflict("local generation cache identity conflicts")
                        saved = self.artifacts.get_bytes(prior.result_artifact_hash)
                        if publication_guard is not None:
                            with self.session_factory.begin() as publication_session:
                                publication_guard(publication_session, investigation_id)
                        return InvestigationResult.model_validate_json(saved).model_copy(
                            update={"is_replayed": True}
                        )
                try:
                    generated = provider.generate(bundle, narrative)
                    validate_generated_narrative(
                        generated.narrative, narrative, generated.plan, question=request.question
                    )
                    if generated.plan is not None:
                        warning_set.add("llm_selected_facts_server_rendered")
                    narrative = generated.narrative
                    prompt_tokens = generated.prompt_tokens
                    output_tokens = generated.output_tokens
                    generation_ms = generated.duration_ms
                    generation_origin = GenerationOrigin.OLLAMA
                    model_digest = PINNED_MANIFEST_DIGEST
                    prompt_hash = provider_prompt_hash
                    request_hash = model_request_hash
                    investigation_id = model_id
                except (GenerationError, InvestigationValidationError) as exc:
                    fallback_reason = (
                        exc.code
                        if isinstance(exc, GenerationError)
                        and re.fullmatch(r"[a-z_]{1,60}", exc.code)
                        else "ollama_support_invalid"
                    )
            else:
                fallback_reason = "ollama_unconfigured"
            if fallback_reason is not None:
                warning_set.add("ollama_template_fallback")
                request_hash = semantic_sha256(
                    {
                        "request_hash": request_hash,
                        "generation_cache": cache_hash,
                        "origin": "template_fallback",
                        "reason": fallback_reason,
                    }
                )
                investigation_id = uuid5(INVESTIGATION_NAMESPACE, request_hash)
        result = InvestigationResult(
            schema_version="1.0",
            investigation_id=investigation_id,
            workspace_id=workspace_id,
            episode_id=episode.id,
            snapshot_id=snapshot.id,
            as_of=as_of,
            temporal_mode=request.temporal_mode,
            retrieval_mode=request.retrieval_mode,
            ranking_mode=actual_ranking_mode,
            generation_origin=generation_origin,
            is_replayed=False,
            fallback_reason=fallback_reason,
            narrative=narrative,
            evidence=list(selected.values()),
            paths=list(paths_by_service.values()),
            ranked_scores=ranked_scores,
            provenance=ResultProvenance(
                template_version=TEMPLATE_VERSION,
                input_manifest_hash=episode.manifest_hash,
                config_hash=semantic_sha256(
                    {
                        "heuristic_version": HEURISTIC_VERSION,
                        "signal_rule_version": report.rule_version,
                        "template_version": TEMPLATE_VERSION,
                        "ranker_artifact_hash": ranker.artifact_hash
                        if ranker is not None
                        else None,
                        "retrieval_mode": request.retrieval_mode.value,
                        "requested_generation_mode": request.generation_mode.value,
                        "generation_cache": cache_hash
                        if request.generation_mode is GenerationMode.OLLAMA
                        else None,
                        "max_evidence": MAX_FINAL_EVIDENCE,
                    }
                ),
                prompt_hash=prompt_hash,
                model_digest=model_digest,
                embedding_revision=MODEL_REVISION,
                feature_schema_version=(
                    f"{report.rule_version}/{ranker.artifact['feature_schema_version']}/"
                    f"{ranker.artifact['model_version']}"
                    if actual_ranking_mode is RankingMode.LEARNED and ranker is not None
                    else f"{report.rule_version}/{HEURISTIC_VERSION}"
                ),
            ),
            timings_ms={
                "signals": round(signals_ms, 3),
                "retrieval_and_corpus": round(retrieval_ms, 3),
                "ranking_and_assembly": round(assembly_ms, 3),
                "generation": generation_ms,
                "total": round((time.perf_counter() - started) * 1000, 3),
            },
            usage=Usage(prompt_tokens=prompt_tokens, output_tokens=output_tokens),
            warnings=sorted(warning_set)[:20],
        )
        validation_started = time.perf_counter()
        validate_result(result, reader, self.artifacts, scores, explanations_by_key)
        result = result.model_copy(
            update={
                "timings_ms": {
                    **result.timings_ms,
                    "validation": round((time.perf_counter() - validation_started) * 1000, 3),
                    "pipeline_before_publication": round((time.perf_counter() - started) * 1000, 3),
                }
            }
        )
        descriptor = self.artifacts.put_bytes(canonical_json(result) + b"\n")
        with self.session_factory.begin() as session:
            if publication_guard is not None:
                publication_guard(session, investigation_id)
            published_snapshot = session.scalar(
                select(SnapshotRow).where(SnapshotRow.id == snapshot.id).with_for_update()
            )
            if published_snapshot is None or published_snapshot.status != "READY":
                raise StorageConflict("investigation snapshot was retired before publication")
            existing = session.get(InvestigationRow, investigation_id)
            if existing is not None:
                if existing.request_hash != request_hash or existing.result_artifact_hash is None:
                    raise StorageConflict("concurrent investigation identity conflicts")
                saved = self.artifacts.get_bytes(existing.result_artifact_hash)
                return InvestigationResult.model_validate_json(saved).model_copy(
                    update={"is_replayed": True}
                )
            register_artifact(
                session,
                workspace_id,
                episode.id,
                descriptor,
                kind="investigation_result",
                redaction_version="derived-from-redacted-v1",
                purpose="investigation_result",
            )
            session.add(
                InvestigationRow(
                    id=investigation_id,
                    workspace_id=workspace_id,
                    episode_id=episode.id,
                    snapshot_id=snapshot.id,
                    status="SUCCEEDED",
                    request_hash=request_hash,
                    result_artifact_hash=descriptor.content_hash,
                )
            )
        return result

    def show(self, workspace_id: UUID, investigation_id: UUID) -> InvestigationResult:
        with self.session_factory() as session:
            row = session.get(InvestigationRow, investigation_id)
            if (
                row is None
                or row.workspace_id != workspace_id
                or row.status != "SUCCEEDED"
                or row.result_artifact_hash is None
            ):
                raise StorageConflict("investigation result is missing or outside scope")
            raw = self.artifacts.get_bytes(row.result_artifact_hash)
        result = InvestigationResult.model_validate_json(raw)
        if result.investigation_id != investigation_id or result.workspace_id != workspace_id:
            raise StorageConflict("investigation result identity differs")
        return result.model_copy(update={"is_replayed": True})

    def evidence(
        self, workspace_id: UUID, investigation_id: UUID, evidence_id: str
    ) -> dict[str, Any]:
        reader = self.reader
        if reader is None:
            raise ValueError("graph reader is required to open evidence")
        result = self.show(workspace_id, investigation_id)
        item = next(
            (evidence for evidence in result.evidence if evidence.evidence_id == evidence_id), None
        )
        if item is None:
            raise StorageConflict("evidence is absent from investigation")
        request = RetrievalRequest(
            workspace_id=workspace_id,
            episode_id=result.episode_id,
            snapshot_id=result.snapshot_id,
            as_of_ns=as_ns(result.as_of),
            mode=_mode(result.temporal_mode),
            question="open saved evidence",
        )
        source = next(
            (
                candidate
                for candidate in corpus_from_reader(reader, self.artifacts, request)
                if candidate.evidence_id == evidence_id
            ),
            None,
        )
        if source is None or source.text != item.text:
            raise StorageConflict("saved evidence no longer resolves to source")
        resolved = evidence_from_candidate(
            source, reader, SpanLocatorResolver(self.artifacts), mode=request.mode
        )
        return {
            "evidence": resolved.model_dump(mode="json"),
            "source": source.source_locator,
            "saved_locator": item.locator.model_dump(mode="json"),
            "locator_repaired": resolved.locator != item.locator,
        }
