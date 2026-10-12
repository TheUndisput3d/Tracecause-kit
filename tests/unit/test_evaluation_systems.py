"""Fair baseline candidate boundaries and prepared-artifact integrity."""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.evaluation import systems
from scripts.evaluation.prepare import selected_inputs

from tracecause.graph.queries import EligibleCall
from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.telemetry.signals import SignalFeature, SignalReport


def episode() -> systems.PublicEpisode:
    workspace, identifier, snapshot, obs_id = uuid4(), uuid4(), uuid4(), uuid4()
    signal = SignalFeature(
        "payment-key",
        obs_id,
        (),
        (),
        1,
        2,
        2,
        3,
        5.0,
        0.0,
        0.0,
        3,
        1.0,
        6,
        100,
        (),
        {},
        "Payment latency",
    )
    report = SignalReport(
        workspace, identifier, "test", "a" * 64, "event", 2, 3, 3, 1, (), (signal,), {}, ()
    )
    corpus = (
        EvidenceCandidate(
            f"observation:{obs_id}",
            "observation",
            "payment latency increased",
            ("unmentioned-key",),
            {"end_ns": 3},
            workspace,
            identifier,
            snapshot,
            3,
        ),
    )
    return systems.PublicEpisode(
        workspace,
        identifier,
        snapshot,
        3,
        report,
        corpus,
        (EligibleCall("storefront-key", "unmentioned-key", (obs_id,)),),
        {"payment": "payment-key", "storefront": "storefront-key", "catalog": "unmentioned-key"},
    )


def test_non_graph_candidate_uses_text_not_graph_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = episode()
    monkeypatch.setattr(
        systems,
        "dense_rank",
        lambda *args: (((data.corpus[0].evidence_id, 1.0),), {"hits": 1, "misses": 0}),
    )
    monkeypatch.setattr(
        systems,
        "model_tokenizer",
        lambda: SimpleNamespace(encode=lambda *args, **kwargs: SimpleNamespace(ids=[1])),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("non-graph baseline called graph candidate generation")

    monkeypatch.setattr(systems, "generate_candidates", forbidden)
    model = SimpleNamespace(artifact={"validation": {"rejection": {"threshold": None}}})
    first = systems.predict(data, "What happened?", "dense", None, model)
    second = systems.predict(replace(data, calls=()), "What happened?", "dense", None, model)
    assert first["candidate_ranking"] == second["candidate_ranking"] == ["payment-key"]
    assert first["context_tokens"] <= systems.CONFIG["context_tokens"]
    assert first["exposed_ranking"] == ["payment-key"]
    assert systems.mentioned_services("prepayment or payment_processor", data.names) == ()


@pytest.mark.parametrize("change", ["hash", "scope", "future", "duplicate"])
def test_prepared_corpus_refuses_corruption(change: str, tmp_path: Path) -> None:
    data = episode()
    raw = {
        "workspace_id": data.workspace_id,
        "episode_id": data.episode_id,
        "snapshot_id": data.snapshot_id,
        "as_of_ns": data.cutoff,
        "signal_report": asdict(data.report),
        "corpus": [asdict(item) for item in data.corpus],
        "calls": [asdict(item) for item in data.calls],
        "service_keys": data.names,
    }
    path = tmp_path / "episode.json"
    if change == "scope":
        raw["corpus"][0]["episode_id"] = uuid4()
    elif change == "future":
        raw["corpus"][0]["source_locator"]["end_ns"] = 4
    elif change == "duplicate":
        raw["corpus"] *= 2
    path.write_text(json.dumps(raw, default=str))
    expected = "b" * 64 if change == "hash" else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        systems.load_episode(path, expected)


def test_preparation_rejects_private_root_and_duplicate_index(tmp_path: Path) -> None:
    private = tmp_path / "gold"
    private.mkdir()
    with pytest.raises(ValueError, match="public"):
        selected_inputs(private)
    (tmp_path / "index.json").write_text(json.dumps({"episodes": [{"episode_id": "same"}] * 2}))
    with pytest.raises(ValueError, match="duplicate"):
        selected_inputs(tmp_path)
