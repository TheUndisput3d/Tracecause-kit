"""Known-answer metric cases and grouped bootstrap invariants."""

import pytest

from tracecause.evaluation.metrics import (
    paired_episode_bootstrap,
    rate,
    score_edges,
    score_ranking,
    score_retrieval,
    summarize_rankings,
)


def test_multiple_accepted_targets_and_candidate_miss() -> None:
    score = score_ranking(
        ["catalog", "checkout", "inventory"], ["payment", "inventory"], identifiable=True
    )
    assert score.candidate_recalled is True
    assert score.first_accepted_rank == 3
    assert score.hit_at_1 is False
    assert score.hit_at_3 is True
    assert score.reciprocal_rank == pytest.approx(1 / 3)
    missed = score_ranking(["catalog"], ["payment"], identifiable=True)
    assert missed.candidate_recalled is False
    assert missed.reciprocal_rank == 0
    assert missed.hit_at_3 is False


def test_healthy_episode_has_separate_denominator() -> None:
    score = score_ranking(["payment"], [], identifiable=False)
    assert score.candidate_recalled is None
    assert score.hit_at_1 is None
    assert rate(0, 0).value is None
    with pytest.raises(ValueError, match="lacks accepted target"):
        score_ranking(["payment"], [], identifiable=True)


def test_retrieval_reports_unjudged_coverage() -> None:
    score = score_retrieval({"direct": 2, "useful": 1, "bad": 0}, ["unknown", "direct", "bad"], k=3)
    assert score.judged_relevant == 2
    assert score.retrieved_relevant == 1
    assert score.recall == 0.5
    assert score.judgment_coverage == pytest.approx(2 / 3)
    assert score.ndcg is not None and 0 < score.ndcg < 1
    empty = score_retrieval({"bad": 0}, [], k=3)
    assert empty.recall is None and empty.ndcg is None
    assert empty.judgment_coverage is None


def test_episode_bootstrap_is_paired_and_repeatable() -> None:
    episodes = {"episode-1": (1.0, 0.0), "episode-2": (0.0, 1.0), "episode-3": (1.0, 0.0)}
    result = paired_episode_bootstrap(episodes, seed=17, resamples=1000)
    assert result == paired_episode_bootstrap(
        dict(reversed(list(episodes.items()))), seed=17, resamples=1000
    )
    assert result.episodes == 3
    assert result.point_difference == pytest.approx(1 / 3)
    assert result.lower <= result.point_difference <= result.upper
    with pytest.raises(ValueError, match="non-finite"):
        paired_episode_bootstrap({"bad": (float("nan"), 0.0)})


def test_ranking_summary_keeps_candidate_misses_and_non_fault_cases_separate() -> None:
    summary = summarize_rankings(
        [
            score_ranking(["payment", "catalog"], ["payment"], identifiable=True),
            score_ranking(["catalog", "payment"], ["payment"], identifiable=True),
            score_ranking(["catalog"], ["payment"], identifiable=True),
            score_ranking([], [], identifiable=False),
            score_ranking(["payment"], ["checkout"], identifiable=False),
        ]
    )
    assert summary.episodes == 5
    assert (summary.identifiable_faults, summary.healthy, summary.unidentifiable_faults) == (
        3,
        1,
        1,
    )
    assert summary.candidate_recall == rate(2, 3)
    assert summary.hit_at_1 == rate(1, 3)
    assert summary.hit_at_3 == rate(2, 3)
    assert summary.conditional_hit_at_1 == rate(1, 2)
    assert summary.conditional_hit_at_3 == rate(2, 2)
    assert summary.mrr == pytest.approx(0.5)
    empty = summarize_rankings([])
    assert empty.mrr is None
    assert empty.candidate_recall.value is None


def test_directed_edge_reconstruction_scores_observable_truth() -> None:
    score = score_edges(
        [("storefront", "checkout"), ("payment", "checkout")],
        [("storefront", "checkout"), ("checkout", "payment")],
    )
    assert score.true_positive_edges == 1
    assert score.precision == rate(1, 2)
    assert score.recall == rate(1, 2)
    with pytest.raises(ValueError, match="duplicate"):
        score_edges([("a", "b"), ("a", "b")], [])
