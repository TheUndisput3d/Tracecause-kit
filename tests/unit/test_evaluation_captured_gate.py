"""Captured test evaluation must wait for all actual inputs and explicit reviews."""

import pytest
from scripts.evaluation.benchmark import CANONICAL_QUERY
from scripts.evaluation.captured_benchmark import select_reviewed, validate_prediction


def test_partial_capture_only_allows_training_smoke() -> None:
    labels = [
        {"episode_id": "train-a", "split": "train"},
        {"episode_id": "test-a", "split": "test"},
    ]
    review = {
        "status": "partial_review",
        "dataset_runs": 2,
        "reviewed_labels": labels,
        "all_test_runs_reviewed": False,
    }
    prepared = {"episodes": [{"episode_id": key, "sha256": key} for key in ("train-a", "test-a")]}
    assert [row["episode_id"] for row in select_reviewed(review, prepared, smoke=True)] == [
        "train-a"
    ]
    with pytest.raises(ValueError, match="all 24"):
        select_reviewed(review, prepared, smoke=False)
    with pytest.raises(ValueError, match="missing"):
        select_reviewed(review, {"episodes": []}, smoke=True)


def test_complete_gate_rejects_missing_review_and_duplicate() -> None:
    labels = [{"episode_id": str(i), "split": "test"} for i in range(24)]
    review = {
        "status": "representative_ai_reviewed",
        "dataset_runs": 24,
        "reviewed_labels": labels,
        "all_test_runs_reviewed": True,
    }
    prepared = {"episodes": [{"episode_id": str(i), "sha256": str(i)} for i in range(24)]}
    assert len(select_reviewed(review, prepared, smoke=False)) == 24
    with pytest.raises(ValueError, match="all 24"):
        select_reviewed({**review, "reviewed_labels": labels[:-1]}, prepared, smoke=False)
    with pytest.raises(ValueError, match="duplicate"):
        select_reviewed(
            review, {"episodes": prepared["episodes"] + prepared["episodes"][:1]}, smoke=False
        )


@pytest.mark.parametrize("key", ["episode_id", "snapshot_id", "as_of_ns", "system", "question"])
def test_saved_capture_prediction_must_match_run_scope(key: str) -> None:
    prediction = dict(
        episode_id="e", snapshot_id="s", as_of_ns=10, system="dense", question=CANONICAL_QUERY
    )
    scope = dict(episode_id="e", snapshot_id="s", cutoff=10, system="dense")
    validate_prediction(prediction, **scope)
    with pytest.raises(ValueError, match="scope, query or system"):
        validate_prediction({**prediction, key: "different"}, **scope)
