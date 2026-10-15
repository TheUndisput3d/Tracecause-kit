"""Validate public OTLP fixtures; private checks are an explicit offline option."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

from scripts.fixtures import GROUPS, validate_split
from tracecause.contracts.canonical import bytes_sha256
from tracecause.contracts.models import EpisodeManifest

TRACE_RE = re.compile(r"^[a-f0-9]{32}$")
SPAN_RE = re.compile(r"^[a-f0-9]{16}$")
FORBIDDEN_PUBLIC = re.compile(
    r'"(?:target_service|mechanism|scenario_group|group_id|intervention_start|split|injector|gold_label)"'
)


def validate_public(public_root: Path) -> dict[str, int]:
    if "gold" in public_root.resolve().parts:
        raise ValueError("runtime-visible public root cannot be under gold")
    if public_root.is_symlink():
        raise ValueError("public fixture root cannot be a symlink")
    index_bytes = (public_root / "index.json").read_bytes()
    if FORBIDDEN_PUBLIC.search(index_bytes.decode()):
        raise ValueError("private label field in public index")
    index = json.loads(index_bytes)
    if set(index) != {"schema_version", "origin", "episodes"}:
        raise ValueError("unexpected public index field")
    if index["schema_version"] != "1.0" or index["origin"] != "synthetic":
        raise ValueError("unsupported public fixture index")
    episode_ids: set[str] = set()
    source_hashes: set[str] = set()
    trace_count = span_count = duplicates = missing_parents = 0
    for entry in index["episodes"]:
        if set(entry) != {"episode_id", "manifest_hash", "source_hash"}:
            raise ValueError("unexpected public episode index field")
        episode_id = str(UUID(entry["episode_id"]))
        if episode_id in episode_ids:
            raise ValueError("duplicate episode ID")
        episode_ids.add(episode_id)
        folder = public_root / episode_id
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("public episode directory must be real")
        if set(path.name for path in folder.iterdir()) != {"manifest.json", "traces.jsonl"}:
            raise ValueError("public episode directory contains undeclared files")
        manifest_bytes = (folder / "manifest.json").read_bytes()
        if FORBIDDEN_PUBLIC.search(manifest_bytes.decode()):
            raise ValueError("private label field in public manifest")
        if bytes_sha256(manifest_bytes) != entry["manifest_hash"]:
            raise ValueError("manifest hash mismatch")
        manifest = EpisodeManifest.model_validate_json(manifest_bytes)
        if str(manifest.episode_id) != episode_id:
            raise ValueError("manifest/episode identity mismatch")
        if len(manifest.sources) != 1 or manifest.sources[0].logical_name != "traces.jsonl":
            raise ValueError("unsupported fixture source list")
        source = folder / manifest.sources[0].logical_name
        if source.is_symlink():
            raise ValueError("symlink fixture source is forbidden")
        source_bytes = source.read_bytes()
        source_hash = bytes_sha256(source_bytes)
        if source_hash != entry["source_hash"] or source_hash != manifest.sources[0].sha256:
            raise ValueError("source hash mismatch")
        if len(source_bytes) != manifest.sources[0].bytes:
            raise ValueError("source byte count mismatch")
        if source_hash in source_hashes:
            raise ValueError("duplicate source hash across episodes")
        source_hashes.add(source_hash)
        for line in source_bytes.splitlines():
            if not line:
                raise ValueError("empty OTLP line")
            envelope = json.loads(line)
            spans: list[dict[str, object]] = []
            for resource in envelope["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    spans.extend(scope["spans"])
            if not spans:
                raise ValueError("empty OTLP trace")
            trace_count += 1
            ids = {str(span["spanId"]) for span in spans}
            seen: dict[str, bytes] = {}
            for span in spans:
                trace_id, span_id = str(span["traceId"]), str(span["spanId"])
                parent_id = str(span["parentSpanId"])
                if not TRACE_RE.fullmatch(trace_id) or not SPAN_RE.fullmatch(span_id):
                    raise ValueError("invalid OTLP ID encoding")
                if set(trace_id) == {"0"} or set(span_id) == {"0"}:
                    raise ValueError("zero OTLP ID")
                if parent_id and not SPAN_RE.fullmatch(parent_id):
                    raise ValueError("invalid parent span ID")
                if int(str(span["endTimeUnixNano"])) < int(str(span["startTimeUnixNano"])):
                    raise ValueError("negative span duration")
                canonical = json.dumps(span, sort_keys=True, separators=(",", ":")).encode()
                if span_id in seen:
                    if seen[span_id] != canonical:
                        raise ValueError("conflicting duplicate span ID")
                    duplicates += 1
                else:
                    seen[span_id] = canonical
                if parent_id and parent_id not in ids:
                    missing_parents += 1
                span_count += 1
    if {path.name for path in public_root.iterdir()} != {"index.json", *episode_ids}:
        raise ValueError("undeclared file in public fixture root")
    return {
        "episodes": len(episode_ids),
        "traces": trace_count,
        "spans_including_duplicates": span_count,
        "identical_duplicates": duplicates,
        "missing_parent_references": missing_parents,
    }


def validate_private(public_root: Path, gold_root: Path) -> dict[str, int]:
    validate_split()
    public = json.loads((public_root / "index.json").read_text())
    private = json.loads((gold_root / "dataset.json").read_text())
    access = json.loads((gold_root / "training_access.json").read_text())
    is_smoke = len(public["episodes"]) == 1
    if private["group_assignments"] != [g.__dict__ for g in GROUPS]:
        raise ValueError("frozen group assignment changed")
    if private["held_out_topology"] != "C":
        raise ValueError("held-out topology changed")
    counts = Counter(item["split"] for item in private["episodes"])
    expected_counts = {"train": 1} if is_smoke else {"train": 70, "dev": 25, "test": 25}
    if counts != expected_counts:
        raise ValueError("episode split counts changed")
    by_id = {item["episode_id"]: item for item in public["episodes"]}
    expected_episodes = 1 if is_smoke else 120
    if len(by_id) != expected_episodes or len(private["episodes"]) != expected_episodes:
        raise ValueError("unexpected private episode count")
    group_counts = Counter(item["group_id"] for item in private["episodes"])
    if not is_smoke and (set(group_counts.values()) != {5} or len(group_counts) != 24):
        raise ValueError("each group must have five episodes")
    expected_groups = {group.group_id: group.split for group in GROUPS}
    split_by_id = {item["episode_id"]: item["split"] for item in private["episodes"]}
    split_by_group: dict[str, str] = {}
    for item in private["episodes"]:
        group_id, split = item["group_id"], item["split"]
        if expected_groups.get(group_id) != split:
            raise ValueError("group assignment differs from frozen split")
        if group_id in split_by_group and split_by_group[group_id] != split:
            raise ValueError("scenario group crosses splits")
        split_by_group[group_id] = split
        for relation in ("paraphrase_of", "adjacent_window_of"):
            related = item.get(relation)
            if related is not None and split_by_id.get(related) != split:
                raise ValueError(f"{relation} crosses splits or references unknown episode")
    for item in private["episodes"]:
        episode_id = item["episode_id"]
        if episode_id not in by_id:
            raise ValueError("private episode missing from public index")
        truth_bytes = (gold_root / episode_id / "truth.json").read_bytes()
        if bytes_sha256(truth_bytes) != item["truth_hash"]:
            raise ValueError("private truth hash mismatch")
        truth = json.loads(truth_bytes)
        if truth["source_hash"] != by_id[episode_id]["source_hash"]:
            raise ValueError("truth/source hash mismatch")
        if truth["scenario_group"] != item["group_id"] or truth["split"] != item["split"]:
            raise ValueError("truth/group mismatch")
        if truth["seed"] != item["seed"]:
            raise ValueError("truth/seed mismatch")
    if access["episodes"] != [
        item for item in private["episodes"] if item["split"] in {"train", "dev"}
    ] or access["groups"] != [
        {"group_id": group.group_id, "split": group.split}
        for group in GROUPS
        if group.split in {"train", "dev"}
    ]:
        raise ValueError("training access manifest differs from frozen train/dev split")
    return {"groups": len(group_counts), "episodes": len(by_id), **dict(counts)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--gold-root", type=Path)
    args = parser.parse_args()
    public = validate_public(args.public_root)
    private = validate_private(args.public_root, args.gold_root) if args.gold_root else None
    print(json.dumps({"public": public, "private": private}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
