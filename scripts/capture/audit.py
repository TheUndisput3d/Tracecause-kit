"""Private, reproducible MiniShop effect audit; never used as model input."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding

from .minishop import MODE_ROLE, PRIVATE, PUBLIC, ROLES, ROOT, SCHEDULE, WORKSPACE_ID, dump, quality
from .validation import frozen_schedule, timestamp_ns

REVIEW_FIELDS = [
    "episode_id",
    "key",
    "split",
    "intended_mode",
    "observed_effect",
    "proposed_targets",
    "service_mapping_confirmed",
    "intervention_confirmed",
    "accepted_targets",
    "identifiable",
    "reviewer",
    "review_date",
    "independent_review_attestation",
    "evidence_notes",
    "disagreement_notes",
]


def _server_windows(source: bytes, episode_id: UUID, intervals: dict[str, str]) -> dict[str, Any]:
    batch = normalize_otlp(
        BytesIO(source),
        encoding=InputEncoding.OTLP_JSONL_V1,
        workspace_id=UUID(WORKSPACE_ID),
        episode_id=episode_id,
        expected_source_hash=hashlib.sha256(source).hexdigest(),
        source_name="traces.jsonl",
    )
    if batch.fatal or batch.rejected_count:
        raise ValueError("invalid captured source")
    starts = {
        "baseline": (intervals["baseline_start"], intervals["baseline_end"]),
        "intervention": (intervals["intervention_start"], intervals["intervention_end"]),
        "recovery": (intervals["recovery_start"], intervals["recovery_end"]),
    }
    ranges = {
        name: (timestamp_ns(left), timestamp_ns(right)) for name, (left, right) in starts.items()
    }
    previous_end = 0
    for left, right in ranges.values():
        if left < previous_end or right <= left:
            raise ValueError("capture stages are overlapping or reversed")
        previous_end = right
    windows: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(lambda: defaultdict(list))
    for span in batch.spans:
        if span.kind != "SERVER" or span.service_name is None:
            continue
        start_ns = int(span.start_ns)
        status = span.safe_attributes.get("http.status_code", 0)
        code = status if isinstance(status, int) else 0
        duration = (int(span.end_ns) - start_ns) / 1e6
        for stage, (left, right) in ranges.items():
            if left <= start_ns < right:
                windows[stage][span.service_name].append((code, duration))
                break
    result: dict[str, Any] = {}
    for stage, services in windows.items():
        result[stage] = {
            role: {
                "count": len(values),
                "http_5xx": sum(code >= 500 for code, _ in values),
                "median_duration_ms": statistics.median(duration for _, duration in values),
            }
            for role, values in services.items()
        }
    return result


def _effect(mode: str, windows: dict[str, Any]) -> tuple[bool | None, str]:
    if mode == "healthy":
        return None, "healthy control; compare stage distributions manually"
    role = MODE_ROLE[mode]
    before = windows.get("baseline", {}).get(role, {})
    during = windows.get("intervention", {}).get(role, {})
    if min(before.get("count", 0), during.get("count", 0)) < 5:
        return None, "insufficient service observations"
    if mode == "inventory_delay":
        increase = during["median_duration_ms"] - before["median_duration_ms"]
        return increase >= 500, f"inventory median duration increase {increase:.1f} ms"
    if mode == "caller_timeout":
        rise = during["http_5xx"] / during["count"] - before["http_5xx"] / before["count"]
        return rise >= 0.2, f"checkout 5xx fraction increase {rise:.3f}; cause remains ambiguous"
    rise = during["http_5xx"] / during["count"] - before["http_5xx"] / before["count"]
    return rise >= 0.2, f"{role} 5xx fraction increase {rise:.3f}"


def _quality_issues(truth: dict[str, Any], windows: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    quality = truth["quality"]
    if quality["fatal"] or quality["accepted_spans"] <= 0:
        issues.append("invalid_or_empty_spans")
    for field in ("rejected_spans", "duplicate_spans", "missing_parents"):
        if quality[field]:
            issues.append(field)
    if set(quality["service_counts"]) != set(ROLES):
        issues.append("missing_service_coverage")
    if quality["direct_client_server_pairs"] <= 0:
        issues.append("missing_direct_client_server_pairs")
    if truth["reset_verified"] is not True:
        issues.append("reset_not_verified")
    for stage, declared in zip(
        ("baseline", "intervention", "recovery"), truth["declared_durations"], strict=True
    ):
        start = datetime.fromisoformat(truth["actual_intervals"][f"{stage}_start"])
        end = datetime.fromisoformat(truth["actual_intervals"][f"{stage}_end"])
        if (end - start).total_seconds() < declared:
            issues.append(f"{stage}_short_duration")
        if set(windows.get(stage, {})) != set(ROLES):
            issues.append(f"{stage}_missing_service")
        load = truth["load"][stage]
        if load["completed"] != load["scheduled"]:
            issues.append(f"{stage}_incomplete_load")
        if load["achieved_rps"] < 1.8:
            issues.append(f"{stage}_low_throughput")
    return issues


def audit_one(truth_path: Path) -> dict[str, Any]:
    truth = json.loads(truth_path.read_text())
    episode_id = UUID(truth["episode_id"])
    public = PUBLIC / str(episode_id)
    manifest = EpisodeManifest.model_validate_json((public / "manifest.json").read_bytes())
    source = (public / "traces.jsonl").read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if (
        truth_path.parent.name != str(episode_id)
        or manifest.episode_id != episode_id
        or str(manifest.workspace_id) != WORKSPACE_ID
        or manifest.origin != "captured_demo"
        or manifest.availability_recorded
        or len(manifest.sources) != 1
        or manifest.sources[0].logical_name != "traces.jsonl"
        or manifest.sources[0].kind != "otlp_jsonl"
        or manifest.sources[0].bytes != len(source)
    ):
        raise ValueError("capture manifest identity or source metadata mismatch")
    if digest != truth["source_sha256"] or digest != manifest.sources[0].sha256:
        raise ValueError("source hash mismatch")
    intervals = truth["actual_intervals"]
    if (
        manifest.baseline_interval is None
        or timestamp_ns(manifest.baseline_interval.start.isoformat())
        != timestamp_ns(intervals["baseline_start"])
        or timestamp_ns(manifest.baseline_interval.end.isoformat())
        != timestamp_ns(intervals["baseline_end"])
        or timestamp_ns(manifest.event_interval.start.isoformat())
        != timestamp_ns(intervals["baseline_start"])
        or timestamp_ns(manifest.event_interval.end.isoformat())
        < timestamp_ns(intervals["recovery_end"])
    ):
        raise ValueError("capture manifest intervals disagree with actual stages")
    if quality(source, episode_id) != truth["quality"]:
        raise ValueError("stored capture quality differs from source measurements")
    windows = _server_windows(source, episode_id, truth["actual_intervals"])
    effect, note = _effect(truth["mode"], windows)
    issues = _quality_issues(truth, windows) if truth["split"] != "smoke" else []
    proposed = [] if truth["mode"] == "healthy" else [MODE_ROLE[truth["mode"]]]
    return {
        "episode_id": str(episode_id),
        "key": truth["key"],
        "split": truth["split"],
        "mode": truth["mode"],
        "seed": truth["seed"],
        "source_sha256": digest,
        "windows": windows,
        "observable_effect": effect,
        "effect_note": note,
        "quality_issues": issues,
        "proposed_target_services": proposed,
        "accepted_target_services": None,
        "human_review_status": truth.get("audit_status", "pending_human_review"),
        "identifiability": "ambiguous" if truth["mode"] == "caller_timeout" else "pending_review",
    }


def audit_all(*, require_full: bool = False) -> dict[str, Any]:
    declared = frozen_schedule(SCHEDULE)
    durations = [declared[f"{stage}_seconds"] for stage in ("baseline", "intervention", "recovery")]
    for path in PRIVATE.glob("*/truth.json"):
        truth = json.loads(path.read_text())
        if truth.get("split") != "smoke" and truth["declared_durations"] != durations:
            raise ValueError("capture durations conflict with frozen schedule")
    runs = [audit_one(path) for path in PRIVATE.glob("*/truth.json")]
    runs.sort(key=lambda item: item["key"])
    keys = [item["key"] for item in runs if item["split"] != "smoke"]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate frozen run key")
    declared_runs = {item["key"]: item for item in declared["runs"]}
    if len(declared_runs) != 24 or len(declared["runs"]) != 24:
        raise ValueError("frozen schedule must contain 24 unique runs")
    for item in runs:
        if item["split"] == "smoke":
            continue
        expected = declared_runs.get(item["key"])
        if (
            expected is None
            or any(item[field] != expected[field] for field in ("split", "seed"))
            or (item["mode"] != expected["mode"])
        ):
            raise ValueError("captured run conflicts with frozen schedule")
    splits = Counter(item["split"] for item in runs if item["split"] != "smoke")
    full = set(keys) == set(declared_runs) and splits == Counter({"train": 12, "dev": 6, "test": 6})
    if require_full and not full:
        raise ValueError("capture dataset remains partial")
    quality_issues = {
        item["key"]: item["quality_issues"]
        for item in runs
        if item["split"] != "smoke" and item["quality_issues"]
    }
    result = {
        "schema_version": "1.0",
        "protocol": declared["protocol"],
        "schedule_sha256": hashlib.sha256(SCHEDULE.read_bytes()).hexdigest(),
        "status": (
            "complete_with_quality_issues_pending_review"
            if full and quality_issues
            else "complete_pending_human_review"
            if full
            else "partial"
        ),
        "benchmark_runs": len(keys),
        "split_counts": dict(splits),
        "smoke_runs": sum(item["split"] == "smoke" for item in runs),
        "mode_counts": dict(Counter(item["mode"] for item in runs if item["split"] != "smoke")),
        "quality_issues": quality_issues,
        "observable_effects": Counter(
            str(item["observable_effect"]) for item in runs if item["split"] != "smoke"
        ),
        "runs": runs,
    }
    review_paths = [ROOT / "reports/capture/review_sheet.csv"]
    ai_path = ROOT / "reports/capture/ai_review_sheet.csv"
    if ai_path.exists():
        review_paths.append(ai_path)
    prepared = [(path, _review_rows(path, runs)) for path in review_paths]
    for review_path, rows_to_write in prepared:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = review_path.with_suffix(".csv.tmp")
        with temporary_path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows_to_write)
        temporary_path.replace(review_path)
    dump(ROOT / "reports/capture/audit.json", result)
    return result


def _review_rows(review_path: Path, runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    existing: dict[str, dict[str, str]] = {}
    if review_path.is_file():
        with review_path.open(newline="") as source:
            reader = csv.DictReader(source)
            columns = reader.fieldnames or []
            if (
                columns[:6] != REVIEW_FIELDS[:6]
                or len(columns) != len(set(columns))
                or any(column not in REVIEW_FIELDS for column in columns)
            ):
                raise ValueError("existing capture review sheet has unexpected columns")
            for row in reader:
                episode_id = row.get("episode_id", "")
                if not episode_id or episode_id in existing:
                    raise ValueError("invalid or duplicate existing review row")
                existing[episode_id] = row
    rows_to_write = []
    for item in runs:
        if item["split"] == "smoke":
            continue
        fixed = {
            "episode_id": item["episode_id"],
            "key": item["key"],
            "split": item["split"],
            "intended_mode": item["mode"],
            "observed_effect": str(item["observable_effect"]),
            "proposed_targets": ";".join(item["proposed_target_services"]),
        }
        previous = existing.pop(item["episode_id"], None)
        if previous is not None and any(previous.get(key) != value for key, value in fixed.items()):
            raise ValueError("existing review row conflicts with frozen audit")
        rows_to_write.append({**(previous or {}), **fixed})
    if existing:
        raise ValueError("review sheet contains an episode absent from audit")
    return rows_to_write


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--require-full", action="store_true")
    args = parser.parse_args()
    report = audit_all(require_full=args.require_full)
    print(json.dumps({key: value for key, value in report.items() if key != "runs"}, default=dict))
