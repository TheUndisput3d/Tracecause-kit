"""Prepare completed public captures at a fixed public-baseline-relative cutoff.

No private injector files, effect labels, review targets or predictions are read.
The incomplete capture and short encoding smoke are never benchmark inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracecause.contracts.models import EpisodeManifest

from ..capture.validation import timestamp_ns
from .prepare import ROOT, digest, prepare, write_json


def index_completed(public_root: Path) -> dict:
    rows = []
    excluded_short = []
    for directory in sorted(public_root.iterdir()):
        if not directory.is_dir():
            continue
        if directory.is_symlink():
            raise ValueError("capture public directory must not be a symlink")
        path = directory / "manifest.json"
        manifest = EpisodeManifest.model_validate_json(path.read_bytes())
        if manifest.origin != "captured_demo" or str(manifest.episode_id) != directory.name:
            raise ValueError("captured public episode identity differs")
        if manifest.baseline_interval is None or len(manifest.sources) != 1:
            raise ValueError("captured evaluation requires a baseline and one source")
        duration = timestamp_ns(manifest.baseline_interval.end.isoformat()) - timestamp_ns(
            manifest.baseline_interval.start.isoformat()
        )
        if duration < 300_000_000_000:
            excluded_short.append(str(manifest.episode_id))
            continue
        source = manifest.sources[0]
        source_path = directory / "traces.jsonl"
        if source.logical_name != "traces.jsonl" or source_path.is_symlink():
            raise ValueError("unexpected captured source")
        source_hash = digest(source_path)
        if source_hash != source.sha256 or source_path.stat().st_size != source.bytes:
            raise ValueError("captured source hash or byte size differs")
        rows.append(
            {
                "episode_id": str(manifest.episode_id),
                "manifest_hash": digest(path),
                "source_hash": source_hash,
            }
        )
    result = {
        "origin": "captured_demo",
        "episodes": rows,
        "excluded_short_baseline_episodes": excluded_short,
        "gold_access": False,
        "partial_declared_capture_set": len(rows) != 24,
    }
    write_json(public_root / "index.json", result)
    return result


def main() -> None:
    public_root = ROOT / "data/captured"
    index = index_completed(public_root)
    result = prepare(
        public_root,
        ROOT / "reports/phase13-captured-prepared-v1",
        ROOT / "reports/phase13-artifacts",
        None,
        database_name="tracecause_evaluation_v1",
        cutoff_after_baseline_seconds=240,
    )
    print(
        json.dumps(
            {**result, "partial_declared_capture_set": index["partial_declared_capture_set"]}
        )
    )


if __name__ == "__main__":
    main()
