"""Benchmark denominators, review boundaries and stable predictions."""

from copy import deepcopy

import pytest
from scripts.evaluation.benchmark import semantic_prediction, summarize, validate_inputs


def test_review_split_overlap_and_duplicate_rejected() -> None:
    question = {
        "question_id": "q1",
        "episode_id": "e1",
        "prepared_sha256": "hash1",
        "split": "train",
        "group_id": "g1",
        "identifiable": True,
        "accepted_target_services": ["payment"],
        "evidence_judgments": [],
    }
    second = {**question, "question_id": "q2", "episode_id": "e2", "prepared_sha256": "hash2"}
    index = {
        "episodes": [
            {"episode_id": "e1", "sha256": "hash1"},
            {"episode_id": "e2", "sha256": "hash2"},
        ]
    }
    review = {"status": "ai_reviewed_source_resolved", "questions": [question, second]}
    validate_inputs(review, index)
    broken = deepcopy(review)
    broken["questions"][1]["split"] = "test"
    with pytest.raises(ValueError, match="overlaps"):
        validate_inputs(broken, index)
    broken = deepcopy(review)
    broken["questions"][1]["question_id"] = "q1"
    with pytest.raises(ValueError, match="duplicate question"):
        validate_inputs(broken, index)
    broken = {**review, "status": "ai_attested_requires_evidence_resolution"}
    with pytest.raises(ValueError, match="source-resolved"):
        validate_inputs(broken, index)


def test_report_keeps_abstention_candidate_miss_and_healthy_denominators() -> None:
    base = {
        "track": "reviewed_question",
        "split": "test",
        "variant": "dense",
        "category": "direct",
        "identifiable": True,
        "candidate_ranking": ["payment"],
        "accepted_target_keys": ["payment"],
        "exposed_ranking": [],
        "retrieval_score": None,
        "cache": {"hits": 1, "misses": 0},
        "timings_ms": {"retrieval": 1, "ranking": 1, "total": 2},
        "generation": {
            "valid_unique_citations": 0,
            "unique_citations": 0,
            "consistent_numeric_claims": 0,
            "numeric_claims": 0,
            "generation_and_source_validation_ms": 1,
        },
    }
    rows = [
        base,
        {**base, "candidate_ranking": ["catalog"], "exposed_ranking": ["catalog"]},
        {**base, "identifiable": False, "accepted_target_keys": [], "exposed_ranking": ["catalog"]},
    ]
    summary = next(row for row in summarize(rows)["groups"] if row["category"] == "all")
    assert summary["ranking"]["hit_at_1"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert summary["ranking"]["conditional_hit_at_1"]["denominator"] == 1
    assert summary["exposed_hit1_identifiable"]["numerator"] == 0
    assert summary["answered_coverage"]["denominator"] == 3
    assert summary["answered_identifiable_top1_error"]["value"] == 1
    assert summary["healthy_hypothesis_rate"]["value"] == 1
    assert summary["citation_validity"]["value"] is None


def test_semantic_reproducibility_ignores_only_measurements() -> None:
    first = {
        "candidate_ranking": ["a"],
        "cache": {"hits": 0},
        "timings_ms": {"total": 100},
        "generation": {
            "narrative": {"status": "supported"},
            "generation_and_source_validation_ms": 10,
        },
    }
    second = {**first, "cache": {"hits": 1}, "timings_ms": {"total": 1}}
    assert semantic_prediction(first) == semantic_prediction(second)
    assert semantic_prediction(first) != semantic_prediction({**second, "candidate_ranking": ["b"]})


def test_only_ambiguous_and_healthy_split_has_no_target_accuracy():
    base = dict(
        track="canonical",
        split="test",
        category="caller_timeout",
        identifiable=False,
        candidate_ranking=["checkout"],
        accepted_target_keys=["checkout"],
        retrieval_score=None,
        cache={"hits": 1, "misses": 0},
        timings_ms={"retrieval": 1, "ranking": 1, "total": 2},
        generation=dict(
            valid_unique_citations=0,
            unique_citations=0,
            consistent_numeric_claims=0,
            numeric_claims=0,
            generation_and_source_validation_ms=1,
        ),
    )
    rows = []
    for system in ("dense", "graph_learned"):
        for episode in ("ambiguous", "healthy"):
            rows.append(
                {
                    **base,
                    "variant": system,
                    "episode_id": episode,
                    "exposed_ranking": ["checkout"] if system == "dense" else [],
                    "accepted_target_keys": [] if episode == "healthy" else ["checkout"],
                }
            )
    report = summarize(rows)
    for group in report["groups"]:
        assert group["ranking"]["hit_at_1"]["denominator"] == 0
        assert group["ranking"]["hit_at_1"]["value"] is None
        assert group["ranking"]["mrr"] is None
    assert len(report["paired_episode_intervals"]) == 1
    interval = report["paired_episode_intervals"][0]
    assert interval["metric"] == "answered_coverage"
    assert interval["episodes"] == 2
    assert interval["point_difference"] == -1
