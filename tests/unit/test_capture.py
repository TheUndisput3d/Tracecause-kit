"""Capture boundary, Collector conformance, and interruption cleanup checks."""

import asyncio
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from scripts.capture import audit, minishop, review, validation
from scripts.capture.audit import _effect, _quality_issues
from scripts.capture.packet import render_packet

from tracecause.storage.postgres import closed_analysis_boundary

FIXTURE = Path(__file__).resolve().parents[2] / "data/fixtures/captured-collector/otlp.jsonl"


def test_review_packet_labels_proposals_and_missing_measurements() -> None:
    document = {
        "status": "partial",
        "benchmark_runs": 1,
        "runs": [
            {"split": "smoke"},
            {
                "episode_id": "00000000-0000-4000-8000-000000000012",
                "key": "payment-01",
                "split": "train",
                "mode": "payment_error",
                "source_sha256": "a" * 64,
                "observable_effect": True,
                "effect_note": "fraction increase 0.5",
                "quality_issues": ["missing_recovery"],
                "proposed_target_services": ["payment"],
                "windows": {
                    "intervention": {
                        "payment": {"count": 20, "http_5xx": 10, "median_duration_ms": 22.25}
                    }
                },
            },
        ],
    }
    result = render_packet(document, "b" * 64)
    assert "not human-reviewed gold" in result
    assert "Proposed targets (unapproved): `payment`" in result
    assert "| intervention | payment | 20 | 10 | 0.5000 | 22.250 |" in result
    assert "| baseline | **missing**" in result
    assert "missing_recovery" in result
    assert result.count("## payment-01") == 1
    assert document["runs"][1]["proposed_target_services"] == ["payment"]


def test_runner_lock_excludes_second_control_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(minishop, "RAW", tmp_path / "raw")
    assert minishop.runner_active() is False
    with minishop.capture_lock():
        assert minishop.runner_active() is True
        with pytest.raises(RuntimeError, match="another capture"):
            with minishop.capture_lock():
                pytest.fail("second capture acquired controls")
    assert minishop.runner_active() is False


def test_successful_reset_command_returns_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(minishop, "RAW", tmp_path / "raw")
    monkeypatch.setattr(sys, "argv", ["minishop", "reset"])
    calls: list[str] = []
    monkeypatch.setattr(minishop, "reset_all", lambda: calls.append("reset"))
    minishop.main()
    assert calls == ["reset"]


def test_real_collector_jsonl_conforms() -> None:
    source = FIXTURE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "2bf7667535df7356c679388237a89bae7c209f96775409b3bb47530cdde54237"
    )
    report = minishop.quality(source, UUID("00000000-0000-4000-8000-000000000012"))
    assert report["accepted_spans"] == 22
    assert report["rejected_spans"] == 0
    assert report["missing_parents"] == 0
    assert report["direct_client_server_pairs"] == 4
    assert set(report["service_counts"]) == set(minishop.ROLES)
    assert b"/__control" not in source
    assert b"payment_error" not in source


def test_partition_uses_root_time_and_keeps_crossing_spans(tmp_path: Path) -> None:
    now = datetime(2026, 10, 11, tzinfo=UTC)
    start = int(now.timestamp() * 1e9)
    trace_a = "a" * 32
    trace_b = "b" * 32
    root = {
        "traceId": trace_a,
        "spanId": "1" * 16,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 2_000_000_000),
    }
    crossing = {
        "traceId": trace_a,
        "spanId": "2" * 16,
        "parentSpanId": "1" * 16,
        "startTimeUnixNano": str(start + 1_000_000_000),
        "endTimeUnixNano": str(start + 3_000_000_000),
    }
    outside = {
        "traceId": trace_b,
        "spanId": "3" * 16,
        "startTimeUnixNano": str(start - 4_000_000_000),
        "endTimeUnixNano": str(start - 3_000_000_000),
    }
    envelope = {"resourceSpans": [{"scopeSpans": [{"spans": [outside, crossing, root]}]}]}
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps(envelope) + "\n")
    selected, counts = minishop.select_complete_traces(raw, now, now + timedelta(seconds=2))
    spans = json.loads(selected)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert {span["traceId"] for span in spans} == {trace_a}
    assert len(spans) == 2
    assert counts == {
        "raw_spans": 3,
        "selected_spans": 2,
        "selected_traces": 1,
        "discarded_spans": 1,
    }


def test_interrupted_run_resets_and_stops_collector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(minishop, "RAW", tmp_path)
    monkeypatch.setattr(minishop, "uuid4", lambda: UUID("00000000-0000-4000-8000-000000000123"))

    def fake_compose(*args: str, episode_file: str | None = None) -> str:
        calls.append("compose:" + ":".join(args))
        return ""

    def fake_reset() -> None:
        calls.append("reset")

    def fake_flush() -> None:
        calls.append("flush")

    def fake_control(*args: object, **kwargs: object) -> dict[str, str]:
        calls.append("intervene")
        return {"status": "set"}

    async def fake_health() -> None:
        calls.append("health")

    async def fake_warm() -> None:
        calls.append("warm")

    async def fake_load(*args: object) -> dict[str, object]:
        calls.append("load")
        if calls.count("load") > 1:
            await asyncio.sleep(0.1)
        return {"completed": 1}

    monkeypatch.setattr(minishop, "compose", fake_compose)
    monkeypatch.setattr(minishop, "reset_all", fake_reset)
    monkeypatch.setattr(minishop, "flush_all", fake_flush)
    monkeypatch.setattr(minishop, "control", fake_control)
    monkeypatch.setattr(minishop, "health", fake_health)
    monkeypatch.setattr(minishop, "warm", fake_warm)
    monkeypatch.setattr(minishop, "load", fake_load)
    with pytest.raises(TimeoutError):
        asyncio.run(
            minishop.run_episode(
                "test", "payment_error", "smoke", 1, (0.01, 0.1, 0.01), interrupt_after=0.01
            )
        )
    assert calls.count("reset") >= 2
    assert calls[-1] == "compose:stop:collector"
    assert "intervene" in calls


def test_unobservable_fault_and_missing_baseline_stay_unidentified() -> None:
    before = {"count": 20, "http_5xx": 0, "median_duration_ms": 20.0}
    unchanged = {"count": 20, "http_5xx": 0, "median_duration_ms": 21.0}
    observed, _ = _effect(
        "payment_error", {"baseline": {"payment": before}, "intervention": {"payment": unchanged}}
    )
    assert observed is False
    insufficient, reason = _effect("payment_error", {"intervention": {"payment": unchanged}})
    assert insufficient is None
    assert reason == "insufficient service observations"


def test_real_stage_end_closes_baseline_before_intervention() -> None:
    baseline_end = 1_791_706_702_065_749_000
    assert closed_analysis_boundary(baseline_end) == 1_791_706_700_000_000_000
    assert closed_analysis_boundary(baseline_end) <= baseline_end
    assert closed_analysis_boundary(1_791_706_710_000_000_000) == 1_791_706_710_000_000_000


def test_audit_preserves_review_and_conflict_cannot_truncate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private = tmp_path / "private"
    truth_path = private / "episode" / "truth.json"
    truth_path.parent.mkdir(parents=True)
    truth_path.write_text(json.dumps({"split": "train", "declared_durations": [300, 300, 120]}))
    schedule = tmp_path / "schedule.json"
    schedule.write_text(
        json.dumps(
            {
                "protocol": "test",
                "baseline_seconds": 300,
                "intervention_seconds": 300,
                "recovery_seconds": 120,
                "runs": [
                    {"key": "payment-01", "mode": "payment_error", "split": "train", "seed": 12001},
                    *(
                        {
                            "key": f"other-{index}",
                            "mode": "healthy",
                            "split": "train",
                            "seed": index,
                        }
                        for index in range(23)
                    ),
                ],
            }
        )
    )
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(audit, "PRIVATE", private)
    monkeypatch.setattr(audit, "SCHEDULE", schedule)
    monkeypatch.setattr(
        validation, "SCHEDULE_SHA256", hashlib.sha256(schedule.read_bytes()).hexdigest()
    )
    episode = {
        "episode_id": "00000000-0000-4000-8000-000000000123",
        "key": "payment-01",
        "split": "train",
        "mode": "payment_error",
        "seed": 12001,
        "observable_effect": True,
        "proposed_target_services": ["payment"],
        "quality_issues": [],
    }
    monkeypatch.setattr(audit, "audit_one", lambda _: episode)
    audit.audit_all()
    sheet = tmp_path / "reports/capture/review_sheet.csv"
    with sheet.open(newline="") as source:
        rows = list(csv.DictReader(source))
    rows[0]["reviewer"] = "independent reviewer"
    rows[0]["accepted_targets"] = "payment"
    with sheet.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    audit.audit_all()
    reviewed_bytes = sheet.read_bytes()
    previous_audit = (tmp_path / "reports/capture/audit.json").read_bytes()
    assert b"independent reviewer" in reviewed_bytes
    episode["mode"] = "catalog_error"
    with pytest.raises(ValueError, match="conflicts"):
        audit.audit_all()
    assert sheet.read_bytes() == reviewed_bytes
    assert (tmp_path / "reports/capture/audit.json").read_bytes() == previous_audit


def test_capture_quality_issues_detect_short_or_incomplete_stage() -> None:
    start = datetime(2026, 10, 11, tzinfo=UTC)
    intervals = {}
    for name in ("baseline", "intervention", "recovery"):
        intervals[f"{name}_start"] = start.isoformat()
        intervals[f"{name}_end"] = (start + timedelta(seconds=2)).isoformat()
    truth = {
        "quality": {
            "fatal": False,
            "accepted_spans": 10,
            "rejected_spans": 0,
            "duplicate_spans": 0,
            "missing_parents": 0,
            "service_counts": dict.fromkeys(minishop.ROLES, 1),
            "direct_client_server_pairs": 1,
        },
        "reset_verified": True,
        "declared_durations": [3, 3, 3],
        "actual_intervals": intervals,
        "load": {
            name: {"completed": 4, "scheduled": 6, "achieved_rps": 1.5}
            for name in ("baseline", "intervention", "recovery")
        },
    }
    windows = {name: dict.fromkeys(minishop.ROLES, {}) for name in truth["load"]}
    issues = _quality_issues(truth, windows)
    assert set(issues) == {
        f"{name}_{reason}"
        for name in truth["load"]
        for reason in ("short_duration", "incomplete_load", "low_throughput")
    }


def test_capture_review_rejects_incomplete_and_accepts_attested_subset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schedule = tmp_path / "schedule.json"
    schedule.write_text(
        json.dumps(
            {
                "runs": [
                    {"key": "payment-01", "mode": "payment_error", "split": "test", "seed": 12001},
                    {"key": "payment-02", "mode": "payment_error", "split": "test", "seed": 12002},
                ]
            }
        )
    )
    monkeypatch.setattr(review, "SCHEDULE", schedule)
    schedule_hash = hashlib.sha256(schedule.read_bytes()).hexdigest()
    monkeypatch.setattr(validation, "SCHEDULE_SHA256", schedule_hash)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "status": "partial",
                "schedule_sha256": schedule_hash,
                "benchmark_runs": 1,
                "runs": [
                    {
                        "episode_id": "episode-1",
                        "key": "payment-01",
                        "split": "test",
                        "seed": 12001,
                        "mode": "payment_error",
                        "observable_effect": True,
                        "proposed_target_services": ["payment"],
                    }
                ],
            }
        )
    )
    sheet = tmp_path / "review.csv"
    output = tmp_path / "validation.json"
    row = {
        "episode_id": "episode-1",
        "key": "payment-01",
        "split": "test",
        "intended_mode": "payment_error",
        "observed_effect": "True",
        "proposed_targets": "payment",
    }

    def write() -> None:
        with sheet.open("w", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=audit.REVIEW_FIELDS)
            writer.writeheader()
            writer.writerow(row)

    write()
    assert review.validate_review(audit_path, sheet, output)["reviewed_runs"] == 0
    row["reviewer"] = "reviewer A"
    write()
    with pytest.raises(ValueError, match="attestation"):
        review.validate_review(audit_path, sheet, output)
    row.update(
        {
            "service_mapping_confirmed": "yes",
            "intervention_confirmed": "yes",
            "accepted_targets": "payment",
            "identifiable": "yes",
            "review_date": "2026-10-11",
            "independent_review_attestation": "yes",
            "evidence_notes": "Payment has new 5xx responses during intervention.",
        }
    )
    write()
    assert review.validate_review(audit_path, sheet, output)["reviewed_runs"] == 1
    assert review.validate_review(audit_path, sheet, output)["all_test_runs_reviewed"] is False
    assert json.loads(output.read_text())["reviewed_labels"][0]["accepted_target_services"] == [
        "payment"
    ]
    with pytest.raises(ValueError, match="ai_reviewed"):
        review.validate_review(audit_path, sheet, output, reviewer_kind="ai")
    row["independent_review_attestation"] = "ai_reviewed"
    row["reviewer"] = "Codex"
    write()
    ai = review.validate_review(audit_path, sheet, output, reviewer_kind="ai")
    assert ai["review_origin"] == "ai" and ai["independent_human_review"] is False
    assert ai["reviewed_runs"] == 1
    with pytest.raises(ValueError, match="attestation"):
        review.validate_review(audit_path, sheet, output)
    previous = output.read_bytes()
    row["intended_mode"] = "catalog_error"
    write()
    with pytest.raises(ValueError, match="frozen audit"):
        review.validate_review(audit_path, sheet, output, reviewer_kind="ai")
    assert output.read_bytes() == previous


def test_capture_schedule_and_timestamp_precision(tmp_path: Path) -> None:
    schedule = tmp_path / "schedule.json"
    schedule.write_bytes(minishop.SCHEDULE.read_bytes())
    assert validation.frozen_schedule(schedule)["baseline_seconds"] == 300
    schedule.write_text(
        schedule.read_text().replace('"baseline_seconds": 300', '"baseline_seconds": 1')
    )
    with pytest.raises(ValueError, match="frozen protocol"):
        validation.frozen_schedule(schedule)
    assert validation.timestamp_ns("2026-10-11T08:18:22.065749Z") == 1791706702065749000
    assert validation.timestamp_ns("2026-10-11T14:03:22.065749+05:45") == 1791706702065749000
    with pytest.raises(ValueError, match="timezone-aware"):
        validation.timestamp_ns("2026-10-11T08:18:22")


@pytest.mark.parametrize("corruption", ["identity", "bytes", "baseline", "quality", "overlap"])
def test_capture_audit_rejects_corrupted_bundle(
    corruption: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    public, private = tmp_path / "public", tmp_path / "private"
    monkeypatch.setattr(minishop, "PUBLIC", public)
    monkeypatch.setattr(minishop, "PRIVATE", private)
    monkeypatch.setattr(audit, "PUBLIC", public)
    source = FIXTURE.read_bytes()
    spans = [
        span
        for line in source.splitlines()
        for resource in json.loads(line)["resourceSpans"]
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    ]
    first_ns = min(int(span["startTimeUnixNano"]) for span in spans)
    start = datetime.fromtimestamp(first_ns // 1_000_000_000 - 1, UTC)
    episode_id = UUID("00000000-0000-4000-8000-000000000012")
    intervals = {}
    for index, stage in enumerate(("baseline", "intervention", "recovery")):
        intervals[f"{stage}_start"] = (start + timedelta(seconds=index * 10)).isoformat()
        intervals[f"{stage}_end"] = (start + timedelta(seconds=(index + 1) * 10)).isoformat()
    minishop.publish(
        FIXTURE,
        episode_id,
        intervals,
        {
            "episode_id": str(episode_id),
            "key": "smoke",
            "split": "smoke",
            "mode": "payment_error",
            "seed": 1,
            "actual_intervals": intervals,
        },
    )
    truth_path = private / str(episode_id) / "truth.json"
    assert audit.audit_one(truth_path)["episode_id"] == str(episode_id)
    manifest_path = public / str(episode_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    truth = json.loads(truth_path.read_text())
    if corruption == "identity":
        manifest["episode_id"] = "00000000-0000-4000-8000-000000000099"
    elif corruption == "bytes":
        manifest["sources"][0]["bytes"] += 1
    elif corruption == "baseline":
        manifest["baseline_interval"]["end"] = (start + timedelta(seconds=9)).isoformat()
    elif corruption == "quality":
        truth["quality"]["accepted_spans"] += 1
    else:
        truth["actual_intervals"]["intervention_start"] = start.isoformat()
    manifest_path.write_text(json.dumps(manifest))
    truth_path.write_text(json.dumps(truth))
    with pytest.raises(ValueError):
        audit.audit_one(truth_path)
