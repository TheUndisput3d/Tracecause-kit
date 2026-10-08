"""Pure evaluation measures with explicit denominators and episode-level uncertainty."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RankingScore:
    identifiable: bool
    accepted_target_count: int
    candidate_recalled: bool | None
    first_accepted_rank: int | None
    hit_at_1: bool | None
    hit_at_3: bool | None
    reciprocal_rank: float | None


def score_ranking(
    candidates: Sequence[str], accepted_targets: Sequence[str], *, identifiable: bool
) -> RankingScore:
    """An absent accepted target is a candidate miss, not an excluded episode."""
    if len(candidates) != len(set(candidates)):
        raise ValueError("duplicate candidate service")
    targets = set(accepted_targets)
    if identifiable and not targets:
        raise ValueError("identifiable episode lacks accepted target")
    if not identifiable:
        return RankingScore(False, len(targets), None, None, None, None, None)
    rank = next(
        (index for index, candidate in enumerate(candidates, 1) if candidate in targets), None
    )
    return RankingScore(
        True,
        len(targets),
        rank is not None,
        rank,
        rank == 1,
        rank is not None and rank <= 3,
        1.0 / rank if rank is not None else 0.0,
    )


@dataclass(frozen=True)
class RetrievalScore:
    judged_relevant: int
    retrieved_relevant: int
    retrieved_count: int
    retrieved_judged: int
    recall: float | None
    ndcg: float | None
    judgment_coverage: float | None


def score_retrieval(
    judgments: Mapping[str, int], ranked: Sequence[str], *, k: int
) -> RetrievalScore:
    """Unjudged results occupy ranks but do not enter the judged-relevant denominator."""
    if k < 1 or len(ranked) != len(set(ranked)):
        raise ValueError("invalid retrieval ranking")
    if any(grade not in {0, 1, 2} for grade in judgments.values()):
        raise ValueError("relevance grades must be 0, 1 or 2")
    selected = ranked[:k]
    relevant = {key for key, grade in judgments.items() if grade > 0}
    retrieved = sum(key in relevant for key in selected)
    gains = [2 ** judgments.get(key, 0) - 1 for key in selected]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted((2**grade - 1 for grade in judgments.values()), reverse=True)[:k]
    idcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal))
    judged_count = sum(key in judgments for key in selected)
    return RetrievalScore(
        len(relevant),
        retrieved,
        len(selected),
        judged_count,
        retrieved / len(relevant) if relevant else None,
        dcg / idcg if idcg else None,
        judged_count / len(selected) if selected else None,
    )


@dataclass(frozen=True)
class DenominatorRate:
    numerator: int
    denominator: int
    value: float | None


def rate(numerator: int, denominator: int) -> DenominatorRate:
    if denominator < 0 or numerator < 0 or numerator > denominator:
        raise ValueError("invalid metric numerator or denominator")
    return DenominatorRate(numerator, denominator, numerator / denominator if denominator else None)


@dataclass(frozen=True)
class RankingSummary:
    episodes: int
    identifiable_faults: int
    healthy: int
    unidentifiable_faults: int
    candidate_recall: DenominatorRate
    hit_at_1: DenominatorRate
    hit_at_3: DenominatorRate
    conditional_hit_at_1: DenominatorRate
    conditional_hit_at_3: DenominatorRate
    reciprocal_rank_sum: float
    mrr: float | None


def summarize_rankings(scores: Sequence[RankingScore]) -> RankingSummary:
    """Keep candidate misses in end-to-end denominators and non-fault cases separate."""
    eligible = [item for item in scores if item.identifiable]
    recalled = [item for item in eligible if item.candidate_recalled]
    hits_1 = sum(item.hit_at_1 is True for item in eligible)
    hits_3 = sum(item.hit_at_3 is True for item in eligible)
    reciprocal_sum = sum(item.reciprocal_rank or 0.0 for item in eligible)
    return RankingSummary(
        episodes=len(scores),
        identifiable_faults=len(eligible),
        healthy=sum(not item.identifiable and item.accepted_target_count == 0 for item in scores),
        unidentifiable_faults=sum(
            not item.identifiable and item.accepted_target_count > 0 for item in scores
        ),
        candidate_recall=rate(len(recalled), len(eligible)),
        hit_at_1=rate(hits_1, len(eligible)),
        hit_at_3=rate(hits_3, len(eligible)),
        conditional_hit_at_1=rate(hits_1, len(recalled)),
        conditional_hit_at_3=rate(hits_3, len(recalled)),
        reciprocal_rank_sum=reciprocal_sum,
        mrr=reciprocal_sum / len(eligible) if eligible else None,
    )


@dataclass(frozen=True)
class EdgeScore:
    predicted_edges: int
    observable_known_edges: int
    true_positive_edges: int
    precision: DenominatorRate
    recall: DenominatorRate


def score_edges(
    predicted: Sequence[tuple[str, str]], observable_known: Sequence[tuple[str, str]]
) -> EdgeScore:
    """Measure directed call reconstruction; only observable truth enters recall."""
    if len(predicted) != len(set(predicted)) or len(observable_known) != len(set(observable_known)):
        raise ValueError("duplicate directed edge")
    hits = len(set(predicted) & set(observable_known))
    return EdgeScore(
        len(predicted),
        len(observable_known),
        hits,
        rate(hits, len(predicted)),
        rate(hits, len(observable_known)),
    )


@dataclass(frozen=True)
class PairedInterval:
    episodes: int
    point_difference: float
    lower: float
    upper: float
    seed: int
    resamples: int


def paired_episode_bootstrap(
    by_episode: Mapping[str, tuple[float, float]],
    *,
    seed: int = 20261010,
    resamples: int = 1000,
    alpha: float = 0.05,
) -> PairedInterval:
    """Sample paired incident outcomes; never resample spans or correlated questions."""
    if not by_episode or resamples < 1 or not 0 < alpha < 1:
        raise ValueError("invalid paired bootstrap inputs")
    if any(not math.isfinite(a) or not math.isfinite(b) for a, b in by_episode.values()):
        raise ValueError("non-finite episode score")
    differences = [a - b for _, (a, b) in sorted(by_episode.items())]
    rng = random.Random(seed)
    count = len(differences)
    draws = sorted(
        sum(differences[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(resamples)
    )
    lo = max(0, math.floor(resamples * alpha / 2))
    hi = min(resamples - 1, math.ceil(resamples * (1 - alpha / 2)) - 1)
    return PairedInterval(count, sum(differences) / count, draws[lo], draws[hi], seed, resamples)
