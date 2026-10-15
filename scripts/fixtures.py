"""Deterministic OTLP/JSON synthetic fixtures and evaluator-only truth."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from uuid import NAMESPACE_URL, UUID, uuid5

from tracecause.contracts.canonical import bytes_sha256, semantic_records_sha256
from tracecause.contracts.models import EpisodeManifest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
TOPOLOGIES: dict[str, dict[str, tuple[str, ...]]] = {
    "A": {
        "storefront": ("checkout",),
        "checkout": ("payment", "catalog"),
        "catalog": ("inventory",),
        "payment": (),
        "inventory": (),
    },
    "B": {
        "storefront": ("checkout",),
        "checkout": ("payment", "catalog"),
        "payment": ("risk",),
        "catalog": ("inventory",),
        "inventory": (),
        "risk": (),
    },
    "C": {
        "storefront": ("checkout",),
        "checkout": ("payment", "catalog"),
        "catalog": ("inventory",),
        "inventory": ("stock",),
        "payment": (),
        "stock": (),
    },
}
OPERATIONS = {
    "storefront": "POST /buy",
    "checkout": "POST /checkout",
    "payment": "POST /authorize",
    "catalog": "GET /quote",
    "inventory": "GET /availability",
    "risk": "POST /assess",
    "stock": "GET /stock",
}


@dataclass(frozen=True)
class ScenarioGroup:
    index: int
    topology: str
    mechanism: str
    target: str | None
    split: str
    traffic_profile: str = "standard"

    @property
    def group_id(self) -> str:
        return f"g{self.index:02d}"


# Frozen before feature/retrieval tuning. This exact assignment is evaluator-only.
GROUPS: tuple[ScenarioGroup, ...] = (
    ScenarioGroup(0, "A", "error", "payment", "train"),
    ScenarioGroup(1, "A", "latency", "inventory", "train"),
    ScenarioGroup(2, "A", "healthy", None, "train", "low"),
    ScenarioGroup(3, "A", "error", "catalog", "train"),
    ScenarioGroup(4, "A", "latency", "checkout", "train"),
    ScenarioGroup(5, "A", "timeout", "checkout", "train"),
    ScenarioGroup(6, "A", "volume", "storefront", "train"),
    ScenarioGroup(7, "B", "healthy", None, "train", "standard"),
    ScenarioGroup(8, "B", "error", "payment", "train"),
    ScenarioGroup(9, "B", "latency", "inventory", "train"),
    ScenarioGroup(10, "B", "error", "catalog", "train"),
    ScenarioGroup(11, "B", "timeout", "payment", "train"),
    ScenarioGroup(12, "B", "volume", "payment", "train"),
    ScenarioGroup(13, "B", "latency", "catalog", "train"),
    ScenarioGroup(14, "A", "timeout", "catalog", "dev"),
    ScenarioGroup(15, "B", "error", "inventory", "dev"),
    ScenarioGroup(16, "A", "healthy", None, "dev", "high"),
    ScenarioGroup(17, "B", "volume", "inventory", "dev"),
    ScenarioGroup(18, "A", "error", "checkout", "dev"),
    ScenarioGroup(19, "C", "error", "payment", "test"),
    ScenarioGroup(20, "C", "latency", "inventory", "test"),
    ScenarioGroup(21, "C", "timeout", "catalog", "test"),
    ScenarioGroup(22, "C", "healthy", None, "test", "standard"),
    ScenarioGroup(23, "C", "volume", "checkout", "test"),
)


def _seed(master_seed: int, group: ScenarioGroup, episode_index: int) -> int:
    material = f"tracecause-fixtures-v1:{master_seed}:{group.index}:{episode_index}"
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


def _id(material: str, length: int) -> str:
    value = hashlib.sha256(material.encode()).hexdigest()[:length]
    assert set(value) != {"0"}
    return value


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError(f"refusing to overwrite different generated content: {path}")
    path.write_bytes(content)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _otlp_span(
    trace_id: str,
    span_id: str,
    parent_id: str | None,
    name: str,
    kind: int,
    start_ns: int,
    end_ns: int,
    errored: bool,
) -> dict[str, object]:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_id or "",
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            {"key": "http.response.status_code", "value": {"intValue": "500" if errored else "200"}}
        ],
        "status": {"code": 2 if errored else 1},
    }


def _trace(
    group: ScenarioGroup,
    seed: int,
    request_index: int,
    start_ns: int,
    rng: Random,
    trace_nonce: int | None = None,
) -> list[tuple[str, dict[str, object]]]:
    nonce = request_index if trace_nonce is None else trace_nonce
    trace_id = _id(f"{seed}:trace:{nonce}", 32)
    spans: list[tuple[str, dict[str, object]]] = []
    counter = 0
    active = request_index >= 30
    topology = TOPOLOGIES[group.topology]

    def span_id() -> str:
        nonlocal counter
        counter += 1
        return _id(f"{seed}:trace:{nonce}:span:{counter}", 16)

    def service(
        name: str,
        parent_client_id: str | None,
        service_start: int,
    ) -> tuple[int, bool]:
        server_id = span_id()
        work_ms = 5 + rng.randrange(0, 26)
        if active and group.mechanism == "latency" and name == group.target:
            work_ms += 100
        cursor = service_start + 1_000_000
        child_failed = False
        for child in topology[name]:
            if active and group.mechanism == "volume" and child == group.target:
                if request_index % 2 == 0:
                    continue
            client_id = span_id()
            client_start = cursor
            # One child clock runs 2 ms ahead of its parent on a few requests.
            skew = -2_000_000 if request_index % 23 == 4 else 1_000_000
            child_end, failed = service(child, client_id, client_start + skew)
            timeout = (
                active
                and group.mechanism == "timeout"
                and name == group.target
                and child == topology[name][0]
                and request_index % 2 == 0
            )
            client_end = (
                min(child_end + 1_000_000, client_start + 10_000_000)
                if timeout
                else child_end + 1_000_000
            )
            client_error = failed or timeout
            spans.append(
                (
                    name,
                    _otlp_span(
                        trace_id,
                        client_id,
                        server_id,
                        OPERATIONS[child],
                        3,
                        client_start,
                        client_end,
                        client_error,
                    ),
                )
            )
            cursor = max(cursor, child_end + 2_000_000)
            child_failed = child_failed or client_error
        own_error = (
            active
            and group.mechanism == "error"
            and name == group.target
            and request_index % 2 == 0
        ) or (request_index % 17 == 7 and name == "catalog" and name != group.target)
        failed = own_error or child_failed
        service_end = max(cursor + work_ms * 1_000_000, service_start + 1_000_000)
        spans.append(
            (
                name,
                _otlp_span(
                    trace_id,
                    server_id,
                    parent_client_id,
                    OPERATIONS[name],
                    2,
                    service_start,
                    service_end,
                    failed,
                ),
            )
        )
        return service_end, failed

    service("storefront", None, start_ns)
    if request_index % 19 in {5, 10}:
        # Keep the child SERVER's parent ID, but omit that CLIENT record.
        for index, (_, span) in enumerate(spans):
            if span["kind"] == 3:
                spans.pop(index)
                break
    if request_index % 13 == 2 and spans:
        spans.append((spans[0][0], spans[0][1].copy()))
    rng.shuffle(spans)
    return spans


def _envelope(spans: list[tuple[str, dict[str, object]]]) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for service_name, span in spans:
        grouped[service_name].append(span)
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": name}},
                        {"key": "service.namespace", "value": {"stringValue": "minishop"}},
                        {"key": "deployment.environment.name", "value": {"stringValue": "demo"}},
                    ]
                },
                "scopeSpans": [{"scope": {"name": "tracecause.synthetic.v1"}, "spans": items}],
            }
            for name, items in sorted(grouped.items())
        ]
    }


def generate_episode(
    group: ScenarioGroup,
    episode_index: int,
    master_seed: int,
    *,
    smoke: bool = False,
) -> tuple[UUID, bytes, bytes, bytes]:
    seed = _seed(master_seed, group, episode_index)
    rng = Random(seed)
    episode_id = uuid5(NAMESPACE_URL, f"tracecause-fixture:{seed}")
    start = BASE_TIME + timedelta(hours=group.index * 4 + episode_index)
    trace_count = 8 if smoke else 60
    baseline_count = 4 if smoke else 30
    lines: list[bytes] = []
    semantic_records: list[dict[str, object]] = []
    for request_index in range(trace_count):
        if group.traffic_profile == "low" and request_index % 2 == 0:
            continue
        if (
            group.mechanism == "volume"
            and group.target == "storefront"
            and request_index >= baseline_count
            and request_index % 2 == 0
        ):
            continue
        for copy_index in range(2 if group.traffic_profile == "high" else 1):
            event_ns = int(start.timestamp()) * 1_000_000_000
            event_ns += request_index * 10_000_000_000 + copy_index * 100_000_000
            event_ns += rng.randrange(0, 5_000_000)
            scenario_index = request_index if not smoke else request_index + 26
            spans = _trace(
                group,
                seed,
                scenario_index,
                event_ns,
                rng,
                trace_nonce=scenario_index + 10_000 * copy_index,
            )
            envelope = _envelope(spans)
            semantic_records.extend({"service_name": name, **span} for name, span in spans)
            lines.append(
                json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            )
    source = b"".join(lines)
    manifest = {
        "schema_version": "1.0",
        "episode_id": str(episode_id),
        "workspace_id": WORKSPACE_ID,
        "display_name": f"Episode {str(episode_id)[:8]}",
        "origin": "synthetic",
        "event_interval": {
            "start": _iso(start),
            "end": _iso(start + timedelta(seconds=trace_count * 10)),
        },
        "baseline_interval": {
            "start": _iso(start),
            "end": _iso(start + timedelta(seconds=baseline_count * 10)),
        },
        "availability_recorded": False,
        "sources": [
            {
                "logical_name": "traces.jsonl",
                "kind": "otlp_jsonl",
                "sha256": bytes_sha256(source),
                "bytes": len(source),
                "published_at": None,
            }
        ],
        "public_notes": "Synthetic trace fixture for parser and evaluation protocol development.",
    }
    EpisodeManifest.model_validate(manifest)
    truth = {
        "schema_version": "1.0",
        "episode_id": str(episode_id),
        "scenario_group": group.group_id,
        "split": group.split,
        "topology": group.topology,
        "mechanism": group.mechanism,
        "target_service": group.target,
        "accepted_target_services": [] if group.target is None else [group.target],
        "identifiable": group.mechanism not in {"healthy", "timeout", "volume"},
        "intervention_start": _iso(start + timedelta(seconds=baseline_count * 10)),
        "seed": seed,
        "source_hash": bytes_sha256(source),
        "semantic_hash": semantic_records_sha256(semantic_records),
    }
    return episode_id, source, _json_bytes(manifest), _json_bytes(truth)


def generate_dataset(
    public_root: Path,
    gold_root: Path,
    master_seed: int,
    *,
    smoke: bool = False,
) -> dict[str, object]:
    selected = [(GROUPS[0], 0)] if smoke else [(group, i) for group in GROUPS for i in range(5)]
    public_index: list[dict[str, str]] = []
    private_index: list[dict[str, object]] = []
    for group, episode_index in selected:
        episode_id, source, manifest_bytes, truth_bytes = generate_episode(
            group,
            episode_index,
            master_seed,
            smoke=smoke,
        )
        public_dir = public_root / str(episode_id)
        gold_dir = gold_root / str(episode_id)
        _write(public_dir / "traces.jsonl", source)
        _write(public_dir / "manifest.json", manifest_bytes)
        _write(gold_dir / "truth.json", truth_bytes)
        public_index.append(
            {
                "episode_id": str(episode_id),
                "manifest_hash": bytes_sha256(manifest_bytes),
                "source_hash": bytes_sha256(source),
            }
        )
        private_index.append(
            {
                "episode_id": str(episode_id),
                "group_id": group.group_id,
                "split": group.split,
                "seed": _seed(master_seed, group, episode_index),
                "truth_hash": bytes_sha256(truth_bytes),
            }
        )
    public_data: dict[str, object] = {
        "schema_version": "1.0",
        "origin": "synthetic",
        "episodes": public_index,
    }
    private_data: dict[str, object] = {
        "schema_version": "1.0",
        "master_seed": master_seed,
        "group_assignments": [group.__dict__ for group in GROUPS],
        "held_out_topology": "C",
        "episodes": private_index,
    }
    _write(public_root / "index.json", _json_bytes(public_data))
    _write(gold_root / "dataset.json", _json_bytes(private_data))
    training_access = {
        "schema_version": "1.0",
        "master_seed": master_seed,
        "groups": [
            {"group_id": group.group_id, "split": group.split}
            for group in GROUPS
            if group.split in {"train", "dev"}
        ],
        "episodes": [item for item in private_index if item["split"] in {"train", "dev"}],
    }
    _write(gold_root / "training_access.json", _json_bytes(training_access))
    return {
        "episodes": len(selected),
        "groups": 1 if smoke else len(GROUPS),
        "public_index_sha256": bytes_sha256(_json_bytes(public_data)),
        "private_index_sha256": bytes_sha256(_json_bytes(private_data)),
    }


def validate_split() -> None:
    assert len(GROUPS) == 24
    assert Counter(g.split for g in GROUPS) == {"train": 14, "dev": 5, "test": 5}
    assert Counter(g.split for g in GROUPS if g.mechanism == "healthy") == {
        "train": 2,
        "dev": 1,
        "test": 1,
    }
    keys = [(g.topology, g.mechanism, g.target, g.traffic_profile) for g in GROUPS]
    assert len(keys) == len(set(keys))
    assert all(g.topology == "C" for g in GROUPS if g.split == "test")
    assert all(g.topology != "C" for g in GROUPS if g.split != "test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--public-root", type=Path)
    parser.add_argument("--gold-root", type=Path)
    args = parser.parse_args()
    validate_split()
    public_root = args.public_root or (
        ROOT / "data/fixtures/smoke" if args.smoke else ROOT / f"data/generated/seed-{args.seed}"
    )
    gold_root = args.gold_root or (
        ROOT / "data/gold/smoke" if args.smoke else ROOT / f"data/gold/seed-{args.seed}"
    )
    if public_root.resolve() == gold_root.resolve() or public_root in gold_root.parents:
        raise ValueError("public and gold roots must be separate")
    result = generate_dataset(public_root, gold_root, args.seed, smoke=args.smoke)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
