"""Local-only MiniShop capture; public OTLP and private intervention truth are separate."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from tracecause.contracts.models import EpisodeManifest
from tracecause.ingestion.normalize import normalize_otlp
from tracecause.ingestion.otlp_json import InputEncoding

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.capture.yaml"
SCHEDULE = ROOT / "configs/capture_schedule.json"
RAW = ROOT / "reports/capture/raw"
PUBLIC = ROOT / "data/captured"
PRIVATE = ROOT / "data/captured_private"
BASE = "http://127.0.0.1:18080"
ROLES = ("storefront", "checkout", "payment", "catalog", "inventory")
DOWNSTREAM = ROLES[1:]
MODE_ROLE = {
    "payment_error": "payment",
    "inventory_delay": "inventory",
    "catalog_error": "catalog",
    "caller_timeout": "checkout",
}
WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
SCHEDULE_SHA256 = "c82dfba25c8ef1c74f7be79f883b57e604990f44fe9e8d3a6b7b0d21faa89992"

CONTROL_SCRIPT = """
import json, pathlib, sys, urllib.request
token = pathlib.Path('/run/secrets/capture_control_token').read_text().strip()
method, path, body = sys.argv[1:4]
data = None if body == '-' else body.encode()
request = urllib.request.Request('http://127.0.0.1:8000/' + path, data=data,
    headers={'X-Capture-Token': token, 'Content-Type': 'application/json'}, method=method)
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


@contextmanager
def capture_lock() -> Iterator[None]:
    """Only one process may change the shared MiniShop controls or Collector."""
    RAW.parent.mkdir(parents=True, exist_ok=True)
    with (RAW.parent / "runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another capture process owns the MiniShop controls") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def runner_active() -> bool:
    try:
        with capture_lock():
            return False
    except RuntimeError:
        return True


def progress(episode_id: UUID, key: str, stage: str) -> None:
    dump(
        RAW.parent / "runner_state.json",
        {
            "pid": os.getpid(),
            "episode_id": str(episode_id),
            "key": key,
            "stage": stage,
            "updated_at": iso(utcnow()),
        },
    )


def load_schedule() -> dict[str, Any]:
    source = SCHEDULE.read_bytes()
    if hashlib.sha256(source).hexdigest() != SCHEDULE_SHA256:
        raise ValueError("capture schedule differs from the frozen protocol")
    return dict(json.loads(source))


def completed_runs(schedule: dict[str, Any], *, verify_sources: bool = False) -> dict[str, str]:
    expected = {run["key"]: run for run in schedule["runs"]}
    durations = [schedule[f"{stage}_seconds"] for stage in ("baseline", "intervention", "recovery")]
    completed: dict[str, str] = {}
    for path in PRIVATE.glob("*/truth.json"):
        truth = json.loads(path.read_text())
        if truth.get("split") == "smoke":
            continue
        key = truth["key"]
        declared = expected.get(key)
        if (
            key in completed
            or declared is None
            or any(truth[field] != declared[field] for field in ("mode", "split", "seed"))
            or truth["declared_durations"] != durations
            or truth["reset_verified"] is not True
        ):
            raise ValueError("completed capture conflicts with frozen protocol")
        episode_id = str(UUID(truth["episode_id"]))
        if path.parent.name != episode_id:
            raise ValueError("private capture path does not match episode")
        public_dir = PUBLIC / episode_id
        manifest = EpisodeManifest.model_validate_json((public_dir / "manifest.json").read_bytes())
        source = public_dir / "traces.jsonl"
        if (
            manifest.episode_id != UUID(episode_id)
            or manifest.sources[0].sha256 != truth["source_sha256"]
            or not source.is_file()
            or source.stat().st_size != manifest.sources[0].bytes
        ):
            raise ValueError("completed capture public source is missing or inconsistent")
        if (
            verify_sources
            and hashlib.sha256(source.read_bytes()).hexdigest() != truth["source_sha256"]
        ):
            raise ValueError("completed capture source hash mismatch")
        completed[key] = episode_id
    return completed


def compose(*args: str, episode_file: str | None = None) -> str:
    env = dict(os.environ)
    if episode_file is not None:
        env["CAPTURE_EPISODE_FILE"] = episode_file
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Compose command failed: {' '.join(args)}: {result.stderr[-1200:]}")
    return result.stdout.strip()


def control(
    role: str, method: str, path: str, body: dict[str, object] | None = None
) -> dict[str, Any]:
    if role not in ROLES or path not in {"__control", "__state", "__flush"}:
        raise ValueError("control target outside MiniShop allowlist")
    payload = "-" if body is None else json.dumps(body, separators=(",", ":"))
    output = compose("exec", "-T", role, "python", "-c", CONTROL_SCRIPT, method, path, payload)
    return dict(json.loads(output))


def reset_all() -> None:
    errors: list[str] = []
    for role in DOWNSTREAM:
        try:
            control(role, "POST", "__control", {"mode": "healthy", "ttl_seconds": 1})
            if control(role, "GET", "__state").get("mode") != "healthy":
                raise RuntimeError("reset state is not healthy")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{role}: {exc}")
    if errors:
        raise RuntimeError("control reset verification failed: " + "; ".join(errors))


def flush_all() -> None:
    for role in ROLES:
        result = control(role, "POST", "__flush")
        if result.get("flushed") is not True:
            raise RuntimeError(f"span flush failed for {role}")


async def health() -> None:
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        for _ in range(30):
            try:
                result = await client.get(BASE + "/health")
                if result.status_code == 200 and result.json().get("role") == "storefront":
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("local MiniShop storefront did not become healthy")


async def warm() -> None:
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        for _ in range(5):
            result = await client.post(BASE + "/buy", json={"product_id": "sku-01", "quantity": 1})
            if result.status_code != 200:
                raise RuntimeError(f"MiniShop warmup failed: {result.status_code}")


async def load(duration: float, rate: float, seed: int) -> dict[str, object]:
    if rate <= 0 or rate > 10 or duration <= 0:
        raise ValueError("load must be positive and capped at 10 requests/s")
    rng = random.Random(seed)
    outcomes: Counter[str] = Counter()
    started = time.monotonic()
    end = started + duration
    tasks: set[asyncio.Task[None]] = set()
    semaphore = asyncio.Semaphore(8)

    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:

        async def send(product: str, quantity: int) -> None:
            async with semaphore:
                try:
                    response = await client.post(
                        BASE + "/buy", json={"product_id": product, "quantity": quantity}
                    )
                    outcomes[str(response.status_code)] += 1
                except httpx.HTTPError:
                    outcomes["send_error"] += 1

        index = 0
        try:
            while time.monotonic() < end:
                task = asyncio.create_task(
                    send(f"sku-{rng.randrange(1, 5):02d}", rng.randrange(1, 6))
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                index += 1
                await asyncio.sleep(max(0, started + index / rate - time.monotonic()))
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - started
    return {
        "scheduled": index,
        "completed": sum(outcomes.values()),
        "outcomes": dict(outcomes),
        "elapsed_seconds": elapsed,
        "achieved_rps": sum(outcomes.values()) / elapsed,
    }


def select_complete_traces(
    raw: Path, start: datetime, end: datetime
) -> tuple[bytes, dict[str, int]]:
    """Select whole traces by their root start; keep crossing spans in the same episode."""
    envelopes = [json.loads(line) for line in raw.read_bytes().splitlines() if line]
    roots: dict[str, int] = {}
    all_spans: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for envelope in envelopes:
        for resource in envelope.get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    trace_id = span.get("traceId", "")
                    if not span.get("parentSpanId"):
                        at = int(span["startTimeUnixNano"])
                        roots[trace_id] = min(roots.get(trace_id, at), at)
                    all_spans.append((envelope, resource, scope, span))
    lower, upper = int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)
    selected = {trace_id for trace_id, at in roots.items() if lower <= at < upper}
    lines: list[bytes] = []
    count = 0
    for envelope in envelopes:
        new_resources = []
        for resource in envelope.get("resourceSpans", []):
            new_scopes = []
            for scope in resource.get("scopeSpans", []):
                spans = [span for span in scope.get("spans", []) if span.get("traceId") in selected]
                if spans:
                    count += len(spans)
                    new_scopes.append({**scope, "spans": spans})
            if new_scopes:
                new_resources.append({**resource, "scopeSpans": new_scopes})
        if new_resources:
            lines.append(
                json.dumps(
                    {**envelope, "resourceSpans": new_resources}, separators=(",", ":")
                ).encode()
                + b"\n"
            )
    return b"".join(lines), {
        "raw_spans": len(all_spans),
        "selected_spans": count,
        "selected_traces": len(selected),
        "discarded_spans": len(all_spans) - count,
    }


def quality(source: bytes, episode_id: UUID) -> dict[str, object]:
    from io import BytesIO

    source_hash = hashlib.sha256(source).hexdigest()
    batch = normalize_otlp(
        BytesIO(source),
        encoding=InputEncoding.OTLP_JSONL_V1,
        workspace_id=UUID(WORKSPACE_ID),
        episode_id=episode_id,
        expected_source_hash=source_hash,
        source_name="traces.jsonl",
    )
    services = Counter(span.service_name for span in batch.spans)
    kinds = Counter(span.kind for span in batch.spans)
    by_id = {(span.trace_id, span.span_id): span for span in batch.spans}
    missing = sum(
        1
        for span in batch.spans
        if span.parent_span_id and (span.trace_id, span.parent_span_id) not in by_id
    )
    linked = sum(
        1
        for span in batch.spans
        if span.kind == "SERVER"
        and span.parent_span_id
        and (parent := by_id.get((span.trace_id, span.parent_span_id))) is not None
        and parent.kind == "CLIENT"
        and parent.service_name != span.service_name
    )
    return {
        "accepted_spans": batch.accepted_count,
        "rejected_spans": batch.rejected_count,
        "duplicate_spans": batch.duplicate_count,
        "fatal": batch.fatal,
        "service_counts": dict(services),
        "kind_counts": dict(kinds),
        "missing_parents": missing,
        "direct_client_server_pairs": linked,
        "diagnostic_codes": [item.code for item in batch.diagnostics],
    }


def publish(
    raw: Path, episode_id: UUID, intervals: dict[str, str], private: dict[str, object]
) -> None:
    source, partition = select_complete_traces(
        raw,
        datetime.fromisoformat(intervals["baseline_start"]),
        datetime.fromisoformat(intervals["recovery_end"]),
    )
    if not source:
        raise RuntimeError("Collector output had no complete in-window traces")
    forbidden = (
        b"/__control",
        b"/__state",
        b"/__flush",
        b"payment_error",
        b"inventory_delay",
        b"catalog_error",
        b"caller_timeout",
    )
    if any(item in source for item in forbidden):
        raise RuntimeError("private control marker leaked into public OTLP")
    stats = quality(source, episode_id)
    service_counts = stats["service_counts"]
    if not isinstance(service_counts, dict):
        raise RuntimeError("invalid service count report")
    if stats["fatal"] or stats["rejected_spans"] or set(service_counts) != set(ROLES):
        raise RuntimeError(f"captured trace quality failed: {stats}")
    digest = hashlib.sha256(source).hexdigest()
    end = max(
        datetime.fromisoformat(intervals["recovery_end"]),
        datetime.fromtimestamp(
            max(
                int(span["endTimeUnixNano"])
                for line in source.splitlines()
                for res in json.loads(line)["resourceSpans"]
                for scope in res["scopeSpans"]
                for span in scope["spans"]
            )
            / 1e9,
            UTC,
        ),
    )
    manifest = {
        "schema_version": "1.0",
        "episode_id": str(episode_id),
        "workspace_id": WORKSPACE_ID,
        "display_name": f"MiniShop capture {str(episode_id)[:8]}",
        "origin": "captured_demo",
        "event_interval": {
            "start": iso(datetime.fromisoformat(intervals["baseline_start"])),
            "end": iso(end + timedelta(seconds=1)),
        },
        "baseline_interval": {
            "start": iso(datetime.fromisoformat(intervals["baseline_start"])),
            "end": iso(datetime.fromisoformat(intervals["baseline_end"])),
        },
        "availability_recorded": False,
        "sources": [
            {
                "logical_name": "traces.jsonl",
                "kind": "otlp_jsonl",
                "sha256": digest,
                "bytes": len(source),
                "published_at": None,
            }
        ],
        "public_notes": "Locally captured MiniShop OTLP spans. Event-time replay only.",
    }
    EpisodeManifest.model_validate(manifest)
    public_dir, private_dir = PUBLIC / str(episode_id), PRIVATE / str(episode_id)
    if public_dir.exists() or private_dir.exists():
        raise FileExistsError("episode UUID already exists")
    public_dir.mkdir(parents=True)
    (public_dir / "traces.jsonl").write_bytes(source)
    dump(public_dir / "manifest.json", manifest)
    dump(
        private_dir / "truth.json",
        {
            **private,
            "source_sha256": digest,
            "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "partition": partition,
            "quality": stats,
            "audit_status": "pending_human_review",
        },
    )


async def run_episode(
    key: str,
    mode: str,
    split: str,
    seed: int,
    durations: tuple[float, float, float],
    *,
    interrupt_after: float | None = None,
) -> UUID:
    if mode not in {*MODE_ROLE, "healthy"} or split not in {"train", "dev", "test", "smoke"}:
        raise ValueError("invalid frozen MiniShop run")
    episode_id = uuid4()
    raw_name = f"{episode_id}.jsonl"
    raw = RAW / raw_name
    if raw.exists():
        raise FileExistsError(raw)
    intervals: dict[str, str] = {}
    rates: dict[str, object] = {}
    reset_verified = False
    try:
        progress(episode_id, key, "preparing")
        compose("up", "-d", "--no-build", episode_file=raw_name)
        await health()
        reset_all()
        await warm()
        flush_all()
        compose("up", "-d", "--force-recreate", "--no-deps", "collector", episode_file=raw_name)
        await asyncio.sleep(1)
        intervals["baseline_start"] = iso(utcnow())
        progress(episode_id, key, "baseline")
        rates["baseline"] = await load(durations[0], 2.0, seed)
        intervals["baseline_end"] = iso(utcnow())
        if mode != "healthy":
            control(
                MODE_ROLE[mode],
                "POST",
                "__control",
                {"mode": mode, "ttl_seconds": max(60, min(900, durations[1] + 90))},
            )
        intervals["intervention_start"] = iso(utcnow())
        progress(episode_id, key, "intervention")
        if interrupt_after is not None:
            await asyncio.wait_for(load(durations[1], 2.0, seed + 1), timeout=interrupt_after)
        else:
            rates["intervention"] = await load(durations[1], 2.0, seed + 1)
        intervals["intervention_end"] = iso(utcnow())
        reset_all()
        intervals["recovery_start"] = iso(utcnow())
        progress(episode_id, key, "recovery")
        rates["recovery"] = await load(durations[2], 2.0, seed + 2)
        intervals["recovery_end"] = iso(utcnow())
    finally:
        progress(episode_id, key, "cleanup")
        try:
            reset_all()
            reset_verified = True
        finally:
            try:
                flush_all()
                await asyncio.sleep(2)
            finally:
                compose("stop", "collector", episode_file=raw_name)
        progress(episode_id, key, "cleanup_verified")
    if not reset_verified or not raw.is_file():
        raise RuntimeError("capture reset or Collector file missing")
    publish(
        raw,
        episode_id,
        intervals,
        {
            "schema_version": "1.0",
            "episode_id": str(episode_id),
            "key": key,
            "mode": mode,
            "target_role": MODE_ROLE.get(mode),
            "split": split,
            "seed": seed,
            "declared_durations": durations,
            "actual_intervals": intervals,
            "load": rates,
            "reset_verified": reset_verified,
            "identifiability": "ambiguous" if mode == "caller_timeout" else "pending_audit",
        },
    )
    progress(episode_id, key, "published")
    print(
        json.dumps(
            {
                "episode_id": str(episode_id),
                "key": key,
                "public": str(PUBLIC / str(episode_id)),
                "private": str(PRIVATE / str(episode_id)),
            }
        )
    )
    return episode_id


def doctor() -> None:
    if not COMPOSE.is_file() or BASE != "http://127.0.0.1:18080":
        raise RuntimeError("capture configuration is not local")
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    memory_kib = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    disk = shutil.disk_usage(ROOT)
    print(
        json.dumps(
            {
                "docker_version": docker.stdout.strip(),
                "memory_available_gib": round(memory_kib / 1048576, 2),
                "disk_free_gib": round(disk.free / 2**30, 2),
                "compose_project": "tracecause-capture",
                "base_url": BASE,
                "schedule_sha256": hashlib.sha256(SCHEDULE.read_bytes()).hexdigest(),
            }
        )
    )


def status() -> None:
    schedule = load_schedule()
    declared = [run["key"] for run in schedule["runs"]]
    completed = completed_runs(schedule)
    active_files = sorted(RAW.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    state_path = RAW.parent / "runner_state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    print(
        json.dumps(
            {
                "declared_runs": len(declared),
                "completed_runs": len(completed),
                "completed_keys": [key for key in declared if key in completed],
                "next_key": next((key for key in declared if key not in completed), None),
                "latest_raw_file": active_files[0].name if active_files else None,
                "latest_raw_bytes": active_files[0].stat().st_size if active_files else None,
                "runner_active": runner_active(),
                "last_runner_state": state,
                "dataset_status": "complete_pending_human_review"
                if len(completed) == len(declared)
                else "partial",
            },
            sort_keys=True,
        )
    )


def run_command(command: str) -> None:
    if command == "reset":
        reset_all()
        print("verified healthy")
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if command in {"smoke", "interrupt-smoke"}:
            asyncio.run(
                run_episode(
                    "smoke",
                    "payment_error",
                    "smoke",
                    12,
                    (3, 6, 3),
                    interrupt_after=2 if command == "interrupt-smoke" else None,
                )
            )
            return
        schedule = load_schedule()
        if len(schedule["runs"]) != 24 or Counter(run["split"] for run in schedule["runs"]) != {
            "train": 12,
            "dev": 6,
            "test": 6,
        }:
            raise RuntimeError("frozen capture schedule is invalid")
        completed = completed_runs(schedule, verify_sources=True)
        for run in schedule["runs"]:
            if run["key"] in completed:
                continue
            asyncio.run(
                run_episode(
                    run["key"],
                    run["mode"],
                    run["split"],
                    run["seed"],
                    (
                        schedule["baseline_seconds"],
                        schedule["intervention_seconds"],
                        schedule["recovery_seconds"],
                    ),
                )
            )
    except KeyboardInterrupt:
        print("Capture interrupted; controls reset and verified in finally", flush=True)
        raise SystemExit(130) from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["doctor", "status", "smoke", "interrupt-smoke", "full", "reset"]
    )
    args = parser.parse_args()
    if args.command == "doctor":
        doctor()
        return
    if args.command == "status":
        status()
        return
    with capture_lock():
        run_command(args.command)


if __name__ == "__main__":
    main()
