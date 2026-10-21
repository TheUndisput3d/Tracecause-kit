"""Select recorded local image IDs only when schema is unchanged; verify saved replay."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
from alembic.script import ScriptDirectory

from scripts.deployment.backup import COMPOSE, container


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    manifest = json.loads(args.manifest.read_text())
    expected = ScriptDirectory(
        str(args.manifest.parent / "source/migrations/postgres")
    ).get_current_head()
    pg = container("postgres")
    observed = subprocess.check_output(
        [
            "docker",
            "exec",
            pg["Id"],
            "psql",
            "-U",
            "postgres",
            "-d",
            "tracecause",
            "-At",
            "-c",
            "SELECT version_num FROM alembic_version",
        ],
        text=True,
    ).strip()
    if expected != observed:
        raise ValueError(
            "automatic selection requires identical schema; review migration or restore"
        )
    with httpx.Client(trust_env=False, timeout=10) as client:
        if not client.get("http://127.0.0.1:13000/api/v1/config").json()["public_read_only"]:
            raise ValueError("stop writes and use read-only mode before image selection")
    workers = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=tracecause-release",
            "--filter",
            "label=com.docker.compose.service=worker",
        ]
    )
    if workers.strip():
        raise ValueError("stop worker before image selection")
    for image in manifest["images"].values():
        inspected = json.loads(
            subprocess.check_output(["docker", "image", "inspect", image["image_id"]])
        )[0]
        if inspected["Id"] != image["image_id"] or inspected["Config"]["Labels"] != image["labels"]:
            raise ValueError("recorded image identity differs")

    def smoke(name: str) -> dict:
        destination = args.output / f"{name}.json"
        subprocess.run(
            [sys.executable, "-m", "scripts.deployment.smoke", "--output", str(destination)],
            check=True,
            timeout=60,
        )
        return json.loads(destination.read_text())

    before = smoke("before")
    env = {
        **os.environ,
        "APP_IMAGE": manifest["images"]["backend"]["image_id"],
        "WEB_IMAGE": manifest["images"]["web"]["image_id"],
        "PUBLIC_READ_ONLY": "true",
    }
    subprocess.run(
        [*COMPOSE, "up", "-d", "--wait", "--no-build", "api", "web"],
        env=env,
        check=True,
        timeout=180,
    )
    after = smoke("after")
    for key in ("episode_id", "snapshot_id", "investigation_id", "result_sha256"):
        if before[key] != after[key]:
            raise ValueError("saved replay changed across image selection")
    path = Path(".env.release")
    updates = {key: env[key] for key in ("APP_IMAGE", "WEB_IMAGE", "PUBLIC_READ_ONLY")}
    updates.update(
        VCS_REF=manifest["images"]["backend"]["labels"]["org.opencontainers.image.revision"],
        RELEASE_VERSION=manifest["version"],
    )
    lines = []
    for line in path.read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(key + "=" + updates[key] if key in updates else line)
    path.write_text("\n".join(lines) + "\n")
    report = {
        "selected_version": manifest["version"],
        "schema_revision": observed,
        "saved_result_equal": True,
        "snapshot_equal": True,
        "scope": "same-schema image selection; no database downgrade or data restore",
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
