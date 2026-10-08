from copy import deepcopy

import pytest
from scripts.evaluation.captured_pairs import validate


def fixture():
    packet = dict(episode_id="episode", mode="caller_timeout", pairs=[dict(pair_id="pair")])
    review = dict(
        episode_id="episode",
        review_origin="ai",
        independent_human_review=False,
        reviewer="Codex",
        review_date="2026-10-12",
        judgments=[dict(pair_id="pair", judgment="unsupported", rationale="Parent ID differs.")],
    )
    return packet, review


def test_negative_pair_review_is_retained():
    packet, review = fixture()
    result = validate(packet, review)
    assert result["reviewed_pairs"] == 1
    assert result["supported_pairs"] == 0


@pytest.mark.parametrize("pairs", [[], [dict(pair_id="pair"), dict(pair_id="pair")]])
def test_empty_or_duplicate_pair_packet_rejected(pairs):
    packet, review = fixture()
    with pytest.raises(ValueError, match="packet"):
        validate({**packet, "pairs": pairs}, review)


@pytest.mark.parametrize("change", ["missing", "duplicate", "identity", "human", "rationale"])
def test_incomplete_or_false_pair_attestation_rejected(change):
    packet, review = fixture()
    review = deepcopy(review)
    if change == "missing":
        review["judgments"] = []
    elif change == "duplicate":
        review["judgments"] *= 2
    elif change == "identity":
        review["episode_id"] = "elsewhere"
    elif change == "human":
        review["independent_human_review"] = True
    elif change == "rationale":
        review["judgments"][0]["rationale"] = " "
    with pytest.raises(ValueError):
        validate(packet, review)
