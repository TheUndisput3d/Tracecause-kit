"""Exercise Docker upload, container boundaries and persistence across stop/up."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

COMPOSE = ["docker", "compose", "-f", "compose.full.yaml"]


def operator_token() -> str:
    return subprocess.check_output(
        [*COMPOSE, "exec", "-T", "api", "cat", "/run/secrets/operator_token"], text=True
    ).strip()


def saved_state(client: httpx.Client) -> dict:
    def pages(path: str) -> list[dict]:
        items, cursor = [], None
        seen = set()
        while True:
            response = client.get(
                path, params={"limit": 100, **({"cursor": cursor} if cursor else {})}
            )
            response.raise_for_status()
            page = response.json()
            items.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return items
            if cursor in seen:
                raise RuntimeError("saved-state pagination cursor repeated")
            seen.add(cursor)

    episodes = pages("/api/v1/episodes")
    snapshots, results = {}, {}
    for episode in episodes:
        identifier = episode["episode_id"]
        snapshots[identifier] = episode["active_snapshot_id"]
        for investigation in pages(f"/api/v1/episodes/{identifier}/investigations"):
            result_id = investigation["investigation_id"]
            view = client.get(f"/api/v1/investigations/{result_id}")
            view.raise_for_status()
            results[result_id] = hashlib.sha256(view.content).hexdigest()
    return {"snapshots": snapshots, "saved_result_hashes": results}


def boundaries() -> list[dict]:
    ids = subprocess.check_output([*COMPOSE, "ps", "--all", "-q"], text=True).split()
    containers = json.loads(subprocess.check_output(["docker", "inspect", *ids]))
    report = []
    for container in containers:
        name = container["Config"]["Labels"]["com.docker.compose.service"]
        state = container["State"]
        if name in {"bootstrap", "models", "migrate", "seed"}:
            assert state["Status"] == "exited" and state["ExitCode"] == 0
        else:
            assert state["Status"] == "running" and state["Health"]["Status"] == "healthy"
        ports = container["HostConfig"]["PortBindings"] or {}
        if name == "web":
            assert ports and all(
                p["HostIp"] == "127.0.0.1" for rows in ports.values() for p in rows
            )
        else:
            assert not ports
        if name in {"api", "worker"}:
            assert container["Config"]["User"] == "10001:10001"
            assert container["HostConfig"]["ReadonlyRootfs"]
            assert not any("admin_secrets" in m.get("Name", "") for m in container["Mounts"])
        if name == "api":
            artifacts = next(
                m
                for m in container["Mounts"]
                if m["Destination"] == "/var/lib/tracecause/artifacts"
            )
            assert not artifacts["RW"]
        report.append({"service": name, "state": state["Status"], "image_id": container["Image"]})
    assert len(report) == 10
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:14000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    token = operator_token()
    with httpx.Client(base_url=args.url, trust_env=False, timeout=30) as client:
        source = Path("data/demo/episode")
        manifest = json.loads((source / "manifest.json").read_bytes())
        manifest["episode_id"] = str(uuid4())
        manifest["display_name"] = "Docker upload verification (synthetic)"
        files = [
            (
                "files",
                (s["logical_name"], (source / s["logical_name"]).read_bytes(), "application/json"),
            )
            for s in manifest["sources"]
        ]
        response = client.post(
            "/api/v1/imports",
            data={"manifest": json.dumps(manifest)},
            files=files,
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        started = time.monotonic()
        while time.monotonic() - started < 120:
            response = client.get("/api/v1/jobs/" + job_id)
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(1)
        else:
            raise RuntimeError("upload worker deadline exceeded")
        (args.output / "import.json").write_text(json.dumps(job, indent=2) + "\n")
        assert job["status"] == "SUCCEEDED", job
        episode = client.get("/api/v1/episodes/" + manifest["episode_id"]).json()
        assert episode["active_snapshot_id"] == job["result_id"]
        graph = client.get(
            "/api/v1/graph",
            params={
                "episode_id": manifest["episode_id"],
                "snapshot_id": episode["active_snapshot_id"],
                "as_of": manifest["event_interval"]["end"],
                "temporal_mode": "event_time_replay",
            },
        )
        graph.raise_for_status()
        assert graph.json()["nodes"] and graph.json()["edges"]
        before = saved_state(client)
        (args.output / "before.json").write_text(json.dumps(before, indent=2) + "\n")
        boundaries()
        with (args.output / "restart.log").open("w") as log:
            subprocess.run([*COMPOSE, "stop"], stdout=log, stderr=log, check=True, timeout=180)
            subprocess.run(
                [*COMPOSE, "build", "api", "web"],
                stdout=log,
                stderr=log,
                check=True,
                timeout=2700,
            )
            subprocess.run(
                [*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "2400"],
                stdout=log,
                stderr=log,
                check=True,
                timeout=2700,
            )
        assert operator_token() == token
        ready = client.get("/health/ready")
        ready.raise_for_status()
        assert not ready.json()["generation_degraded"]
        after = saved_state(client)
        assert after == before
        report = {
            "passed": True,
            "import_job_id": job_id,
            "credentials_preserved": True,
            "snapshots_preserved": len(after["snapshots"]),
            "saved_results_unchanged": len(after["saved_result_hashes"]),
            "containers": boundaries(),
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
