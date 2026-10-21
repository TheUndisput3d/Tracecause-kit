import json
from collections import Counter
from pathlib import Path

import pytest
from scripts.fixtures import GROUPS, TOPOLOGIES, generate_dataset, generate_episode, validate_split
from scripts.validate_fixtures import validate_private, validate_public

from tracecause.contracts.canonical import bytes_sha256, semantic_records_sha256
from tracecause.contracts.models import EpisodeManifest


def _flatten(source: bytes) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in source.splitlines():
        envelope = json.loads(line)
        for resource in envelope["resourceSpans"]:
            attrs = resource["resource"]["attributes"]
            name = next(a["value"]["stringValue"] for a in attrs if a["key"] == "service.name")
            for scope in resource["scopeSpans"]:
                records.extend({"service_name": name, **span} for span in scope["spans"])
    return records


def test_frozen_split_counts_and_held_out_topology() -> None:
    validate_split()
    assert Counter(g.split for g in GROUPS) == {"train": 14, "dev": 5, "test": 5}
    assert {g.topology for g in GROUPS if g.split == "test"} == {"C"}
    assert Counter(g.split for g in GROUPS if g.mechanism == "healthy") == {
        "train": 2,
        "dev": 1,
        "test": 1,
    }
    assert [5 * sum(g.split == split for g in GROUPS) for split in ("train", "dev", "test")] == [
        70,
        25,
        25,
    ]


def test_same_seed_same_bytes_and_different_seed_changes_source() -> None:
    first = generate_episode(GROUPS[0], 0, 20261010, smoke=True)
    same = generate_episode(GROUPS[0], 0, 20261010, smoke=True)
    changed = generate_episode(GROUPS[0], 0, 20261011, smoke=True)
    assert first == same
    assert first[1] != changed[1]
    assert bytes_sha256(first[1]) != bytes_sha256(changed[1])


def test_delivery_order_does_not_change_semantic_hash() -> None:
    _, source, _, truth_bytes = generate_episode(GROUPS[0], 0, 20261010, smoke=True)
    truth = json.loads(truth_bytes)
    lines = [json.loads(line) for line in source.splitlines()]
    scrambled = []
    for envelope in reversed(lines):
        envelope["resourceSpans"].reverse()
        for resource in envelope["resourceSpans"]:
            resource["scopeSpans"][0]["spans"].reverse()
        scrambled.append(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
    reordered = b"".join(scrambled)
    assert source != reordered
    assert bytes_sha256(source) != bytes_sha256(reordered)
    assert semantic_records_sha256(_flatten(source)) == truth["semantic_hash"]
    assert semantic_records_sha256(_flatten(reordered)) == truth["semantic_hash"]


def test_public_metadata_has_no_target_or_injector_labels() -> None:
    _, source, manifest_bytes, truth_bytes = generate_episode(GROUPS[0], 0, 20261010, smoke=True)
    manifest = EpisodeManifest.model_validate_json(manifest_bytes)
    assert manifest.sources[0].sha256 == bytes_sha256(source)
    assert manifest.sources[0].bytes == len(source)
    public = manifest_bytes.decode()
    for hidden in (
        "target_service",
        "mechanism",
        "scenario_group",
        "intervention_start",
        "payment",
    ):
        assert hidden not in public
    assert b'"target_service"' in truth_bytes


def test_observed_parent_edges_match_declared_topology() -> None:
    _, source, _, _ = generate_episode(GROUPS[0], 0, 20261010, smoke=True)
    allowed = {
        (parent, child) for parent, children in TOPOLOGIES["A"].items() for child in children
    }
    observed: set[tuple[str, str]] = set()
    for line in source.splitlines():
        envelope = json.loads(line)
        by_id: dict[str, tuple[str, dict[str, object]]] = {}
        for resource in envelope["resourceSpans"]:
            name = next(
                a["value"]["stringValue"]
                for a in resource["resource"]["attributes"]
                if a["key"] == "service.name"
            )
            for span in resource["scopeSpans"][0]["spans"]:
                by_id[span["spanId"]] = (name, span)
        for name, span in by_id.values():
            if span["kind"] != 2:
                continue
            parent = by_id.get(span["parentSpanId"])
            if parent and parent[1]["kind"] == 3 and parent[0] != name:
                observed.add((parent[0], name))
    assert observed == allowed


def test_public_validator_detects_duplicates_missing_parent_and_tamper(tmp_path: Path) -> None:
    public_root, gold_root = tmp_path / "public", tmp_path / "private"
    generate_dataset(public_root, gold_root, 20261010, smoke=True)
    report = validate_public(public_root)
    assert report["episodes"] == 1
    assert report["identical_duplicates"] > 0
    assert report["missing_parent_references"] > 0
    episode_id = json.loads((public_root / "index.json").read_text())["episodes"][0]["episode_id"]
    source = public_root / episode_id / "traces.jsonl"
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_public(public_root)


def test_public_validator_refuses_gold_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="under gold"):
        validate_public(tmp_path / "gold" / "fixtures")


def test_private_validator_checks_smoke_truth_hash_and_related_splits(tmp_path: Path) -> None:
    public_root, gold_root = tmp_path / "public", tmp_path / "private"
    generate_dataset(public_root, gold_root, 20261010, smoke=True)
    assert validate_private(public_root, gold_root)["episodes"] == 1
    dataset_path = gold_root / "dataset.json"
    dataset = json.loads(dataset_path.read_text())
    dataset["episodes"][0]["paraphrase_of"] = "unknown-episode"
    dataset_path.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="paraphrase_of crosses splits"):
        validate_private(public_root, gold_root)
    dataset["episodes"][0].pop("paraphrase_of")
    dataset_path.write_text(json.dumps(dataset))
    episode_id = dataset["episodes"][0]["episode_id"]
    truth_path = gold_root / episode_id / "truth.json"
    truth_path.write_bytes(truth_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="private truth hash mismatch"):
        validate_private(public_root, gold_root)


def test_private_validator_rejects_changed_training_access(tmp_path: Path) -> None:
    public_root, gold_root = tmp_path / "public", tmp_path / "private"
    generate_dataset(public_root, gold_root, 20261010, smoke=True)
    path = gold_root / "training_access.json"
    access = json.loads(path.read_text())
    access["episodes"] = []
    path.write_text(json.dumps(access))
    with pytest.raises(ValueError, match="training access manifest differs"):
        validate_private(public_root, gold_root)


def test_draft_evidence_locators_exist_in_smoke_fixture() -> None:
    root = Path(__file__).resolve().parents[2]
    annotations = json.loads((root / "data/evaluation/annotation_examples.json").read_text())
    assert annotations["status"] == "draft_requires_independent_human_review"
    episode_id = annotations["episode_id"]
    source = root / "data/fixtures/smoke" / episode_id / "traces.jsonl"
    span_ids = {
        (span["traceId"], span["spanId"])
        for line in source.read_text().splitlines()
        for resource in json.loads(line)["resourceSpans"]
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    }
    for example in annotations["examples"]:
        assert example["review_status"] == "draft_requires_independent_human_review"
        for judgment in example["evidence_judgments"]:
            assert (judgment["trace_id"], judgment["span_id"]) in span_ids
