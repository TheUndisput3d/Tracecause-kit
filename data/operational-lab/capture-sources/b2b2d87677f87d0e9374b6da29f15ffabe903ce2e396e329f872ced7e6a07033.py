"""Capture three actual local application architectures and their operational receipts.

No Docker daemon, external service or paid API is used. Every HTTP service runs in
its own subprocess on an ephemeral loopback port. Expected outcomes are written
only to the private evaluation directory; public bundles contain observed data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.capture.minishop import WORKSPACE_ID, dump, quality
from tracecause.operational.models import OperationalBundle

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "scripts/capture/operational_lab_runtime.py"
_PORT_LOCK = threading.Lock()
_ALLOCATED_PORTS: set[int] = set()
_ARTIFACT_LOCK = threading.Lock()
APPLICATIONS = (
    {
        "id": "shop-chain",
        "name": "Parcel Shop",
        "topology": "synchronous-chain",
        "roles": ("checkout", "orders", "ledger"),
        "entry": "checkout",
        "target": "ledger",
        "team": "commerce-platform",
    },
    {
        "id": "news-fanout",
        "name": "Newsroom",
        "topology": "parallel-fanout",
        "roles": ("portal", "search", "pricing", "recommendations"),
        "entry": "portal",
        "target": "search",
        "team": "discovery-platform",
    },
    {
        "id": "media-pipeline",
        "name": "Media Works",
        "topology": "asynchronous-queue",
        "roles": ("uploader", "broker", "worker", "transcoder", "object-store"),
        "entry": "uploader",
        "target": "transcoder",
        "team": "media-platform",
    },
)
RELEASES = {
    "1.0.0": 'VERSION = "1.0.0"\n\ndef accept(request_number: int) -> bool:\n    return True\n',
    "1.0.1": (
        'VERSION = "1.0.1"\n\ndef accept(request_number: int) -> bool:\n'
        "    return request_number >= 0\n"
    ),
    "1.1.0": (
        'VERSION = "1.1.0"\n\ndef accept(request_number: int) -> bool:\n'
        "    return request_number % 2 == 0\n"
    ),
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def free_port() -> int:
    with _PORT_LOCK:
        while True:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = int(listener.getsockname()[1])
            if port not in _ALLOCATED_PORTS:
                _ALLOCATED_PORTS.add(port)
                return port


def http(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(json.loads(response.read()))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(json.loads(exc.read()))


@dataclass
class Process:
    process: subprocess.Popen[bytes]
    runtime_id: str
    version: str


class Application:
    def __init__(self, spec: dict[str, Any], episode: Path, artifacts: Path) -> None:
        self.spec, self.episode, self.artifacts = spec, episode, artifacts
        self.episode.mkdir(parents=True, exist_ok=False)
        self.ports = {role: free_port() for role in spec["roles"]}
        self.processes: dict[str, Process] = {}
        self.events: list[dict[str, Any]] = []
        self.host = "local-host-" + sha(platform.node().encode())[:12]
        self.commands: list[dict[str, Any]] = []
        for role in spec["roles"]:
            dump(self.episode / f"{role}.config.json", {"max_batch_items": 64})

    def event(self, kind: str, role: str, details: dict[str, Any], at: str | None = None) -> str:
        identifier = str(uuid4())
        self.events.append(
            {
                "id": identifier,
                "kind": kind,
                "occurred_at": at or utcnow(),
                "available_at": utcnow(),
                "service": {
                    "namespace": self.spec["id"],
                    "environment": "operational-lab",
                    "name": role,
                },
                "source": "tracecause operational-lab process supervisor receipt v1",
                "details": details,
            }
        )
        return identifier

    def stop(self, role: str) -> None:
        active = self.processes.pop(role, None)
        if active is not None:
            active.process.terminate()
            try:
                active.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                active.process.kill()
                active.process.wait(timeout=5)
                raise RuntimeError(f"lab service did not stop cleanly: {role}") from None
            if active.process.returncode != 0:
                raise RuntimeError(f"lab service exited abnormally: {role}")

    def deploy(self, role: str, version: str) -> str:
        self.stop(role)
        started = utcnow()
        source = RELEASES[version].encode()
        source_hash = sha(source)
        release_path = self.artifacts / f"{source_hash}.py"
        with _ARTIFACT_LOCK:
            release_path.parent.mkdir(parents=True, exist_ok=True)
            if not release_path.exists():
                release_path.write_bytes(source)
            if sha(release_path.read_bytes()) != source_hash:
                raise ValueError("existing release artifact differs from its hash")
        runtime_id = f"process-{uuid4()}"
        dependencies = {key: f"http://127.0.0.1:{port}" for key, port in self.ports.items()}
        command = [
            sys.executable,
            str(RUNTIME),
            "--application",
            self.spec["id"],
            "--role",
            role,
            "--port",
            str(self.ports[role]),
            "--instance",
            runtime_id,
            "--release-file",
            str(release_path),
            "--config-file",
            str(self.episode / f"{role}.config.json"),
            "--trace-file",
            str(self.episode / f"{role}.jsonl"),
            "--dependencies",
            json.dumps(dependencies),
        ]
        with (self.episode / f"{role}.log").open("ab") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT)
        self.processes[role] = Process(process, runtime_id, version)
        self.commands.append({"command": command, "pid": process.pid, "started_at": started})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"lab service exited during startup: {role}")
            try:
                status, health = http(f"{dependencies[role]}/health")
                if status == 200 and health == {"service": role, "version": version}:
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            raise RuntimeError(f"lab service did not become healthy: {role}")
        self.event(
            "runtime",
            role,
            {
                "runtime_id": runtime_id,
                "host": self.host,
                "process_id": process.pid,
                "runtime_type": "process",
                "version": version,
                "platform": "CPython " + platform.python_version(),
            },
            started,
        )
        return self.event(
            "deployment",
            role,
            {
                "version": version,
                "revision": source_hash[:12],
                "artifact_sha256": source_hash,
                "runtime_id": runtime_id,
            },
            started,
        )

    def configure(self, role: str, value: int) -> str:
        path = self.episode / f"{role}.config.json"
        previous = json.loads(path.read_text())["max_batch_items"]
        dump(path, {"max_batch_items": value})
        return self.event(
            "configuration",
            role,
            {
                "key": "max_batch_items",
                "previous_value": previous,
                "value": value,
                "runtime_id": self.processes[role].runtime_id,
            },
        )

    def start(self) -> None:
        # The consumer starts last so its first queue polling cannot precede the broker.
        roles = sorted(self.spec["roles"], key=lambda role: role == "worker")
        for role in roles:
            self.deploy(role, "1.0.0")
            self.event(
                "ownership",
                role,
                {
                    "team": self.spec["team"],
                },
            )
            self.configure(role, 64)

    def close(self) -> None:
        errors = []
        roles = sorted(self.processes, key=lambda role: role != "worker")
        for role in roles:
            try:
                self.stop(role)
            except RuntimeError as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def workload(self, seconds: float, rate: float = 3.0) -> dict[str, Any]:
        started, deadline = time.monotonic(), time.monotonic() + seconds
        outcomes: Counter[str] = Counter()
        total = 0
        base = f"http://127.0.0.1:{self.ports[self.spec['entry']]}"
        while time.monotonic() < deadline:
            body = {"request_number": total, "batch_items": 2}
            status, result = http(base + "/work", body)
            if self.spec["topology"] == "asynchronous-queue" and status == 202:
                job = str(result["job_id"])
                poll_deadline = time.monotonic() + 5
                while time.monotonic() < poll_deadline:
                    _, response = http(
                        f"http://127.0.0.1:{self.ports['broker']}/result", {"job_id": job}
                    )
                    if response["status"] is not None:
                        status = int(response["status"])
                        break
                    time.sleep(0.05)
                else:
                    raise RuntimeError("asynchronous job did not finish")
            outcomes[str(status)] += 1
            total += 1
            time.sleep(max(0, started + total / rate - time.monotonic()))
        return {
            "completed": total,
            "outcomes": dict(outcomes),
            "elapsed_seconds": time.monotonic() - started,
        }


def capture_episode(
    spec: dict[str, Any],
    ordinal: int,
    output: Path,
    private: Path,
    durations: tuple[float, float, float],
) -> dict[str, Any]:
    episode_id = uuid4()
    public = output / "episodes" / str(episode_id)
    private_dir = private / str(episode_id)
    app = Application(spec, private_dir / "raw", output / "artifacts")
    event_start = utcnow()
    stages: dict[str, Any] = {}
    target = spec["target"]
    try:
        app.start()
        baseline_start = utcnow()
        stages["baseline"] = app.workload(durations[0])
        baseline_end = utcnow()
        # Scheduling the change before a new ten-second bucket makes event order
        # inspectable. The next workload starts after the boundary, using real time.
        boundary = (int(time.time()) // 10 + 1) * 10
        if boundary - time.time() < 3:
            boundary += 10
        time.sleep(max(0, boundary - time.time() - 2))
        if ordinal == 1:
            changed_id = app.deploy(target, "1.1.0")
            distractor_id = app.deploy(spec["entry"], "1.0.1")
        else:
            changed_id = app.configure(target, 1)
            distractor_id = app.configure(spec["entry"], 128)
        time.sleep(max(0, boundary - time.time() + 0.01))
        analysis_start = utcnow()
        stages["changed"] = app.workload(durations[1])
        analysis_end = utcnow()
        if ordinal == 1:
            app.deploy(target, "1.0.0")
        else:
            app.configure(target, 64)
        recovery_start = utcnow()
        stages["recovery"] = app.workload(durations[2])
        recovery_end = utcnow()
        changed_errors = sum(
            count for status, count in stages["changed"]["outcomes"].items() if int(status) >= 500
        )
        recovery_errors = sum(
            count for status, count in stages["recovery"]["outcomes"].items() if int(status) >= 500
        )
        baseline_errors = sum(
            count for status, count in stages["baseline"]["outcomes"].items() if int(status) >= 500
        )
        if baseline_errors or recovery_errors or changed_errors == 0:
            raise RuntimeError(
                "controlled capture did not show clean baseline, change and recovery"
            )
        app.event(
            "incident_note",
            spec["entry"],
            {
                "title": f"{spec['name']} capture {ordinal} observed outcomes",
                "summary": f"Observed {changed_errors} failed business operations among "
                f"{stages['changed']['completed']} completed operations in the change window. "
                f"Recovery observed {recovery_errors} failures among "
                f"{stages['recovery']['completed']} operations. These are local controlled "
                "measurements, not a causal attribution or production incident.",
            },
        )
    finally:
        app.close()
    event_end = utcnow()
    source = b"".join(path.read_bytes() for path in sorted((private_dir / "raw").glob("*.jsonl")))
    stats = quality(source, episode_id)
    if stats["fatal"] or stats["rejected_spans"] or stats["missing_parents"]:
        raise RuntimeError(f"capture source integrity failed: {stats}")
    public.mkdir(parents=True, exist_ok=False)
    (public / "traces.jsonl").write_bytes(source)
    operational_bundle = OperationalBundle.model_validate(
        {
            "schema_version": "1.0",
            "application_id": spec["id"],
            "application_name": spec["name"],
            "topology": spec["topology"],
            "events": app.events,
        }
    )
    dump(
        public / "operational.json",
        operational_bundle.model_dump(mode="json"),
    )
    operational = (public / "operational.json").read_bytes()
    dump(
        public / "manifest.json",
        {
            "schema_version": "1.0",
            "episode_id": str(episode_id),
            "workspace_id": WORKSPACE_ID,
            "display_name": f"{spec['name']} — captured incident {ordinal}",
            "origin": "captured_demo",
            "event_interval": {"start": event_start, "end": event_end},
            "baseline_interval": {"start": baseline_start, "end": baseline_end},
            "availability_recorded": False,
            "sources": [
                {
                    "logical_name": "traces.jsonl",
                    "kind": "otlp_jsonl",
                    "sha256": sha(source),
                    "bytes": len(source),
                    "published_at": None,
                },
                {
                    "logical_name": "operational.json",
                    "kind": "operational_json",
                    "sha256": sha(operational),
                    "bytes": len(operational),
                    "published_at": None,
                },
            ],
            "public_notes": "Actual localhost HTTP subprocess execution with manually instrumented "
            "OTLP export and process-supervisor operational receipts. Controlled lab "
            "data; operator-assigned ownership. No production or causal claims.",
        },
    )
    dump(
        private_dir / "truth.json",
        {
            "episode_id": str(episode_id),
            "application_id": spec["id"],
            "ordinal": ordinal,
            "target_service": target,
            "target_event_id": changed_id,
            "distractor_event_id": distractor_id,
            "intervention": "release" if ordinal == 1 else "config",
            "intervals": {
                "analysis_start": analysis_start,
                "analysis_end": analysis_end,
                "recovery_start": recovery_start,
                "recovery_end": recovery_end,
            },
            "stages": stages,
            "quality": stats,
            "commands": app.commands,
            "source_sha256": sha(source),
            "operational_sha256": sha(operational),
            "review_status": "machine-checked controlled experiment; no independent human labels",
        },
    )
    result = {
        "episode_id": str(episode_id),
        "application_id": spec["id"],
        "application_name": spec["name"],
        "topology": spec["topology"],
        "ordinal": ordinal,
        "symptom_service": spec["entry"],
        "event_start": event_start,
        "event_end": event_end,
        "analysis_start": analysis_start,
        "analysis_end": analysis_end,
        "accepted_spans": stats["accepted_spans"],
        "source_sha256": sha(source),
        "operational_sha256": sha(operational),
        "operational_events": len(app.events),
    }
    print(json.dumps({"event": "capture_complete", **result}), flush=True)
    return result


def capture_application(
    spec: dict[str, Any], output: Path, private: Path, durations: tuple[float, float, float]
) -> list[dict[str, Any]]:
    return [capture_episode(spec, ordinal, output, private, durations) for ordinal in (1, 2)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/operational-lab")
    parser.add_argument("--private", type=Path, default=ROOT / "reports/operational-lab/private")
    parser.add_argument("--baseline-seconds", type=float, default=70)
    parser.add_argument("--change-seconds", type=float, default=60)
    parser.add_argument("--recovery-seconds", type=float, default=20)
    args = parser.parse_args()
    if (args.output / "index.json").exists():
        raise FileExistsError("capture index exists; choose a new output directory")
    durations = args.baseline_seconds, args.change_seconds, args.recovery_seconds
    if any(duration <= 0 or duration > 600 for duration in durations):
        raise ValueError("each capture stage must be positive and at most 600 seconds")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(
                capture_application, spec, args.output.resolve(), args.private.resolve(), durations
            )
            for spec in APPLICATIONS
        ]
        episodes = [episode for future in futures for episode in future.result()]
    dump(
        args.output / "index.json",
        {
            "schema_version": "1.0",
            "origin": "captured_demo",
            "captured_at": utcnow(),
            "capture_script_sha256": sha(Path(__file__).read_bytes()),
            "runtime_script_sha256": sha(RUNTIME.read_bytes()),
            "episodes": episodes,
            "limitations": [
                "Controlled local workloads, not production traces.",
                "Two chronological episodes per application; small repeated sample.",
                "Ownership is operator-assigned; all services share one real host.",
                "Manual OTLP export records real calls and parent propagation.",
                "Version artifacts are deliberately tiny controlled implementations.",
            ],
        },
    )


if __name__ == "__main__":
    main()
