"""Explicit claim judgments require complete, unchanged cited claims."""

from copy import deepcopy

import pytest
from scripts.evaluation.claim_review import captured_packet, validate
from scripts.evaluation.systems import SYSTEMS


def packet_review():
    claim = {"claim_id": "c", "claim_type": "hypothesis", "evidence_ids": ["e"]}
    run = {
        "run_id": "r",
        "episode_id": "episode",
        "system": "dense",
        "category": "direct",
        "generation": {"narrative": {"claims": [claim]}},
    }
    review = {
        "review_origin": "ai",
        "independent_human_review": False,
        "reviewer": "Codex",
        "review_date": "2026-10-12",
        "reviews": [
            {
                **{key: run[key] for key in ("run_id", "episode_id", "system", "category")},
                "claims": [{**claim, "judgment": "partial", "rationale": "Baseline is not cited."}],
            }
        ],
    }
    return {"runs": [run]}, review


def test_partial_claim_is_not_counted_as_fully_supported() -> None:
    packet, review = packet_review()
    summary = validate(packet, review)
    assert summary["strict_support_denominator"] == 1
    assert summary["strict_support_rate"] == 0
    assert summary["claim_counts"] == {"partial": 1}
    assert summary["structural_citation_coverage"] == 1


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "citation", "rationale", "attestation"]
)
def test_claim_review_rejects_incomplete_or_changed_claim(mutation: str) -> None:
    packet, original = packet_review()
    review = deepcopy(original)
    claims = review["reviews"][0]["claims"]
    if mutation == "missing":
        claims.clear()
    elif mutation == "duplicate":
        claims.append(dict(claims[0]))
    elif mutation == "citation":
        claims[0]["evidence_ids"] = ["invented"]
    elif mutation == "rationale":
        claims[0]["rationale"] = ""
    else:
        review["independent_human_review"] = True
    with pytest.raises(ValueError):
        validate(packet, review)


def captured_rows():
    return [
        dict(
            run_id=f"{mode}-{key}-{system}",
            episode_id=f"{mode}-{key}",
            category=mode,
            group_id=f"{mode}-{key}",
            track="canonical",
            system=system,
        )
        for mode in (
            "payment_error",
            "inventory_delay",
            "catalog_error",
            "caller_timeout",
            "healthy",
        )
        for key in ("02", "01")
        for system in SYSTEMS
    ]


def test_captured_claim_selection_is_fixed_and_complete():
    packet = captured_packet(captured_rows())
    assert len(packet["runs"]) == 25
    assert all(row["group_id"].endswith("01") for row in packet["runs"])
    assert packet == captured_packet(list(reversed(captured_rows())))


@pytest.mark.parametrize("mutation", ["mode", "system", "duplicate", "episode", "track"])
def test_captured_claim_packet_rejects_incomplete_or_mixed_runs(mutation):
    rows = captured_rows()
    if mutation == "mode":
        rows = [r for r in rows if r["category"] != "healthy"]
    elif mutation == "system":
        rows = [r for r in rows if r["system"] != "dense"]
    elif mutation == "duplicate":
        rows.append(dict(rows[0]))
    else:
        row = next(r for r in rows if r["group_id"].endswith("01"))
        row["episode_id" if mutation == "episode" else "track"] = "different"
    with pytest.raises(ValueError):
        captured_packet(rows)


def test_empty_claim_packet_is_not_a_review():
    _, review = packet_review()
    with pytest.raises(ValueError, match="empty"):
        validate({"runs": []}, {**review, "reviews": []})
