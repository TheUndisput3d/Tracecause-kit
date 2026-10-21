"""Back up the owned read-only release using private bridge access and a verified export."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

COMPOSE = ["docker", "compose", "--env-file", ".env.release", "-f", "compose.release.yaml"]


def container(service: str) -> dict:
    identifier = subprocess.check_output([*COMPOSE, "ps", "-q", service], text=True).strip()
    if not identifier:
        raise ValueError(f"release {service} is not running")
    return json.loads(subprocess.check_output(["docker", "inspect", identifier]))[0]


def address(item: dict) -> str:
    addresses = [
        v["IPAddress"]
        for k, v in item["NetworkSettings"]["Networks"].items()
        if k.endswith("_data_net")
    ]
    if len(addresses) != 1 or not addresses[0]:
        raise ValueError("one private release data network is required")
    return addresses[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:13000")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("backup output must be new")
    with httpx.Client(trust_env=False, timeout=10) as client:
        config = client.get(args.url + "/api/v1/config")
        config.raise_for_status()
        if not config.json()["public_read_only"]:
            raise ValueError("switch release API to read-only and stop writers first")
    running = subprocess.check_output(
        [*COMPOSE, "--profile", "operator", "ps", "--status", "running", "--services"], text=True
    ).splitlines()
    if "worker" in running:
        raise ValueError("stop the release worker before backup")
    for service in ("seed", "migrate"):
        active = subprocess.check_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project=tracecause-release",
                "--filter",
                "label=com.docker.compose.service=" + service,
            ]
        )
        if active.strip():
            raise ValueError("wait for release seed/migration tools before backup")
    pg, graph, api = container("postgres"), container("neo4j"), container("api")
    export = args.output.with_name(args.output.name + "-source-artifacts").absolute()
    export.mkdir(parents=True)
    subprocess.run(
        ["docker", "cp", api["Id"] + ":/var/lib/tracecause/artifacts/.", str(export)], check=True
    )
    env = {
        **os.environ,
        "TRACECAUSE_PG_HOST": address(pg),
        "TRACECAUSE_PG_PORT": "5432",
        "TRACECAUSE_PG_DATABASE": "tracecause",
        "TRACECAUSE_PG_USER": "postgres",
        "TRACECAUSE_PG_PASSWORD_FILE": str(
            Path(".secrets/release/postgres_admin_password").absolute()
        ),
        "TRACECAUSE_NEO4J_URI": "bolt://" + address(graph) + ":7687",
        "TRACECAUSE_NEO4J_AUTH_FILE": str(Path(".secrets/release/neo4j_auth").absolute()),
        "TRACECAUSE_ARTIFACT_ROOT": str(export),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.backup",
            "--output",
            str(args.output),
            "--postgres-container",
            pg["Id"],
        ],
        env=env,
        check=True,
        timeout=240,
    )


if __name__ == "__main__":
    main()
