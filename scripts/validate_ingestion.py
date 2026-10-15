"""Reproducible offline normalization sweep over public trace fixtures."""

import argparse
import json
from pathlib import Path

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding
from tracecause.ingestion.source_allowlist import resolve_episode_sources


def sweep(public_root: Path) -> dict[str, int]:
    index = json.loads((public_root / "index.json").read_text())
    totals = {
        "episodes": 0,
        "accepted": 0,
        "rejected": 0,
        "duplicates": 0,
        "conflicts": 0,
        "missing_parent": 0,
        "fatal": 0,
    }
    for entry in index["episodes"]:
        directory = public_root / entry["episode_id"]
        manifest = EpisodeManifest.model_validate_json((directory / "manifest.json").read_bytes())
        sources = resolve_episode_sources(directory, manifest)
        if len(sources) != 1 or manifest.sources[0].kind != "otlp_jsonl":
            raise ValueError("sweep requires one OTLP JSONL source per episode")
        with sources[0].open("rb") as reader:
            batch = normalize_otlp(
                reader,
                encoding=InputEncoding.OTLP_JSONL_V1,
                workspace_id=manifest.workspace_id,
                episode_id=manifest.episode_id,
                expected_source_hash=manifest.sources[0].sha256,
                source_name=manifest.sources[0].logical_name,
            )
        totals["episodes"] += 1
        totals["accepted"] += batch.accepted_count
        totals["rejected"] += batch.rejected_count
        totals["duplicates"] += batch.duplicate_count
        totals["conflicts"] += batch.conflict_count
        totals["missing_parent"] += sum(
            "missing_parent" in span.quality_flags for span in batch.spans
        )
        totals["fatal"] += int(batch.fatal)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, required=True)
    args = parser.parse_args()
    result = sweep(args.public_root)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["rejected"] or result["conflicts"] or result["fatal"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
