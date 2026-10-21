"""Start a new local stack, then restore its data into isolated empty resources.

Uses final already-built images. Only model-cache bytes may be copied from the
source project; application data, credentials, database and graphs start empty.
The owned drill containers are stopped on success; their volumes remain intact.
"""

import argparse
import copy
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
CACHE_COPY_SCRIPT = """import os
import shutil
shutil.copytree('/source', '/target', dirs_exist_ok=True, symlinks=True)
for root, dirs, files in os.walk('/target'):
    os.chown(root, 10001, 10001)
    for name in files:
        os.chown(os.path.join(root, name), 10001, 10001, follow_symlinks=False)
"""


def output(command: list[str], **kwargs) -> str:
    return subprocess.check_output(command, text=True, cwd=ROOT, **kwargs).strip()


def check_ports(ports: tuple[int, ...]) -> None:
    if len(set(ports)) != len(ports) or any(not 1024 <= port <= 65535 for port in ports):
        raise ValueError("drill ports must be distinct nonprivileged ports")
    for port in ports:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


def new_project(project: str) -> None:
    if not re.fullmatch(r"tracecause-proof-[a-z0-9-]{1,40}", project):
        raise ValueError("drill requires a unique tracecause-proof- project")
    if output(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]):
        raise ValueError("drill refuses an existing project")
    if any(
        name.startswith(project + "_")
        for name in output(["docker", "volume", "ls", "-q"]).splitlines()
    ):
        raise ValueError("drill refuses existing project volumes")


def validate_resume(destination: Path, project: str) -> None:
    """Resume only the quiesced, audited startup boundary of our own drill."""
    if not re.fullmatch(r"tracecause-proof-[a-z0-9-]{1,40}", project):
        raise ValueError("resume requires an owned drill project")
    if any(
        (destination / name).exists()
        for name in ("before.json", "backup", "after.json", "report.json")
    ):
        raise ValueError("resume refuses an already-started recovery transaction")
    view = json.loads((destination / "fresh-api.json").read_bytes())
    if view["ready"].get("generation_degraded") or len(view["episodes"]["items"]) != 7:
        raise ValueError("resume requires verified fresh API readiness")
    if not (destination / "source-artifacts" / "sha256").is_dir():
        raise ValueError("resume requires the copied fresh source artifacts")
    identifiers = output(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
    ).split()
    if len(identifiers) != 10:
        raise ValueError("resume requires exactly the isolated startup containers")
    containers = json.loads(output(["docker", "inspect", *identifiers]))
    for item in containers:
        labels = item["Config"]["Labels"]
        if labels["com.docker.compose.project"] != project:
            raise ValueError("resume container ownership differs")
        service, state = labels["com.docker.compose.service"], item["State"]
        if service in {"postgres", "neo4j"}:
            if state["Status"] != "running" or state.get("Health", {}).get("Status") != "healthy":
                raise ValueError("resume source databases must be healthy")
        elif state["Status"] != "exited":
            raise ValueError("resume requires stopped writers and model service")
        if service in {"bootstrap", "models", "migrate", "seed"} and state["ExitCode"] != 0:
            raise ValueError("resume startup job did not pass")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-project", default="tracecause-full")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=14001)
    parser.add_argument("--pg-port", type=int, default=25434)
    parser.add_argument("--bolt-port", type=int, default=27689)
    parser.add_argument("--restore-bolt-port", type=int, default=27690)
    parser.add_argument("--resume-after-startup", action="store_true")
    args = parser.parse_args()
    destination = args.output.absolute()
    private = destination / "private"
    if args.resume_after_startup:
        validate_resume(destination, args.project)
    else:
        new_project(args.project)
        check_ports((args.port, args.pg_port, args.bolt_port, args.restore_bolt_port))
        destination.mkdir(parents=True, mode=0o700)
        private.mkdir(mode=0o700)
    base = yaml.safe_load((ROOT / "compose.full.yaml").read_text())
    restore_graph = copy.deepcopy(base["services"]["neo4j"])
    restore_graph["volumes"] = [
        "restore_graph:/data",
        "restore_graph_logs:/logs",
        "app_secrets:/run/secrets:ro",
    ]
    restore_graph["ports"] = [f"127.0.0.1:{args.restore_bolt_port}:7687"]
    restore_graph["networks"] = ["data_net", "diagnostic_net"]
    override = destination / "compose.drill.yaml"
    override.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "postgres": {
                        "ports": [f"127.0.0.1:{args.pg_port}:5432"],
                        "networks": ["data_net", "diagnostic_net"],
                    },
                    "neo4j": {
                        "ports": [f"127.0.0.1:{args.bolt_port}:7687"],
                        "networks": ["data_net", "diagnostic_net"],
                    },
                    "restore-neo4j": restore_graph,
                },
                "volumes": {"restore_graph": {}, "restore_graph_logs": {}},
                "networks": {"diagnostic_net": {}},
            }
        )
    )
    environment = {**os.environ, "FULL_WEB_PORT": str(args.port)}
    compose = [
        "docker",
        "compose",
        "-p",
        args.project,
        "-f",
        str(ROOT / "compose.full.yaml"),
        "-f",
        str(override),
    ]

    def run(command: list[str], log_name: str, *, env=environment, timeout=180) -> None:
        with (destination / log_name).open("a") as log:
            subprocess.run(
                command, cwd=ROOT, env=env, stdout=log, stderr=log, check=True, timeout=timeout
            )

    def container(service: str) -> str:
        value = output([*compose, "ps", "-aq", service], env=environment)
        if not value or "\n" in value:
            raise ValueError("expected one isolated service container")
        return value

    if not args.resume_after_startup:
        run([*compose, "up", "--no-build", "--abort-on-container-exit", "bootstrap"], "startup.log")
        cache_copies = []
        for name in ("hf_cache", "ollama_models"):
            source, target = f"{args.source_project}_{name}", f"{args.project}_{name}"
            output(["docker", "volume", "inspect", source])
            output(["docker", "volume", "create", target])
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    "0:0",
                    "--cap-drop",
                    "ALL",
                    "--cap-add",
                    "DAC_OVERRIDE",
                    "--cap-add",
                    "CHOWN",
                    "--cap-add",
                    "FOWNER",
                    "-v",
                    f"{source}:/source:ro",
                    "-v",
                    f"{target}:/target",
                    base["services"]["bootstrap"]["image"],
                    "python",
                    "-c",
                    CACHE_COPY_SCRIPT,
                ],
                "cache-copy.log",
                timeout=600,
            )
            cache_copies.append(
                {"kind": name, "source_read_only": source, "fresh_destination": target}
            )
        run(
            [
                *compose,
                "up",
                "--no-build",
                "-d",
                "--wait",
                "--wait-timeout",
                "2400",
                *base["services"],
            ],
            "startup.log",
            timeout=2700,
        )
        with httpx.Client(
            base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=60
        ) as client:
            ready = client.get("/health/ready")
            ready.raise_for_status()
            if ready.json().get("generation_degraded"):
                raise ValueError("fresh stack local generation readiness is degraded")
            episodes = client.get("/api/v1/episodes", params={"limit": 100})
            episodes.raise_for_status()
            if len(episodes.json()["items"]) != 7:
                raise ValueError("fresh stack must contain the demo and six operational episodes")
            (destination / "fresh-api.json").write_text(
                json.dumps({"ready": ready.json(), "episodes": episodes.json()}, indent=2) + "\n"
            )
        containers = json.loads(
            output(["docker", "inspect", *output([*compose, "ps", "-aq"], env=environment).split()])
        )
        for item in containers:
            service = item["Config"]["Labels"]["com.docker.compose.service"]
            status = item["State"]
            if service in {"bootstrap", "migrate", "models", "seed"}:
                if status["Status"] != "exited" or status["ExitCode"] != 0:
                    raise ValueError("fresh bootstrap stage failed")
            elif (
                status["Status"] != "running" or status.get("Health", {}).get("Status") != "healthy"
            ):
                raise ValueError("fresh runtime service is unhealthy")
        run([*compose, "stop", "web", "api", "worker", "ollama"], "quiesce.log", timeout=240)
    else:
        containers = json.loads(
            output(["docker", "inspect", *output([*compose, "ps", "-aq"], env=environment).split()])
        )
        cache_copies = [
            {
                "kind": name,
                "source_read_only": f"{args.source_project}_{name}",
                "fresh_destination": f"{args.project}_{name}",
            }
            for name in ("hf_cache", "ollama_models")
        ]
    if args.resume_after_startup:
        run(
            [
                *compose,
                "up",
                "--no-build",
                "--no-deps",
                "-d",
                "--wait",
                "--wait-timeout",
                "240",
                "postgres",
                "neo4j",
            ],
            "diagnostic-network.log",
            timeout=300,
        )
    pg = container("postgres")
    for name, path in (
        ("postgres_admin_password", "/run/admin-secrets/postgres_admin_password"),
        ("neo4j_auth", "/run/secrets/neo4j_auth"),
        ("operator_token", "/run/secrets/operator_token"),
    ):
        secret = private / name
        secret.write_bytes(subprocess.check_output(["docker", "exec", pg, "cat", path]))
        secret.chmod(0o600)
    source_artifacts = destination / "source-artifacts"
    if not args.resume_after_startup:
        run(
            [
                "docker",
                "cp",
                f"{container('api')}:/var/lib/tracecause/artifacts",
                str(source_artifacts),
            ],
            "artifact-copy.log",
            timeout=300,
        )
    host = {
        **os.environ,
        "TRACECAUSE_PG_HOST": "127.0.0.1",
        "TRACECAUSE_PG_PORT": str(args.pg_port),
        "TRACECAUSE_PG_DATABASE": "tracecause",
        "TRACECAUSE_PG_USER": "postgres",
        "TRACECAUSE_PG_PASSWORD_FILE": str(private / "postgres_admin_password"),
        "TRACECAUSE_NEO4J_URI": f"bolt://127.0.0.1:{args.bolt_port}",
        "TRACECAUSE_NEO4J_AUTH_FILE": str(private / "neo4j_auth"),
        "TRACECAUSE_OPERATOR_TOKEN_FILE": str(private / "operator_token"),
        "TRACECAUSE_ARTIFACT_ROOT": str(source_artifacts),
        "TRACECAUSE_STAGING_ROOT": str(private / "staging"),
        "TRACECAUSE_PUBLIC_READ_ONLY": "false",
        "TRACECAUSE_ENABLE_LOCAL_MODEL": "false",
    }
    run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.operational_audit",
            "--report",
            str(destination / "before.json"),
        ],
        "before-audit.log",
        env=host,
        timeout=1200,
    )
    backup = destination / "backup"
    run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.backup",
            "--output",
            str(backup),
            "--postgres-container",
            pg,
        ],
        "backup.log",
        env=host,
        timeout=1200,
    )
    run([*compose, "stop", "neo4j"], "source-graph-stop.log", timeout=120)
    run(
        [
            *compose,
            "up",
            "--no-build",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "240",
            "restore-neo4j",
        ],
        "restore-graph.log",
        timeout=300,
    )
    database = "tracecause_restore_" + args.project.removeprefix("tracecause-proof-").replace(
        "-", "_"
    )
    restored = {
        **host,
        "TRACECAUSE_PG_DATABASE": database,
        "TRACECAUSE_NEO4J_URI": f"bolt://127.0.0.1:{args.restore_bolt_port}",
        "TRACECAUSE_ARTIFACT_ROOT": str(destination / "restored-artifacts"),
    }
    run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.restore",
            "--backup",
            str(backup),
            "--database",
            database,
            "--postgres-container",
            pg,
            "--report",
            str(destination / "restore.json"),
        ],
        "restore.log",
        env=restored,
        timeout=1800,
    )
    run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.operational_audit",
            "--report",
            str(destination / "after.json"),
        ],
        "after-audit.log",
        env=restored,
        timeout=1200,
    )
    before = json.loads((destination / "before.json").read_bytes())
    after = json.loads((destination / "after.json").read_bytes())
    if before != after:
        raise ValueError("restored snapshots, historical proofs or saved results differ")
    run(
        [
            sys.executable,
            "-m",
            "scripts.recovery.replay",
            "--report",
            str(destination / "saved-replay.json"),
        ],
        "saved-replay.log",
        env=restored,
        timeout=600,
    )
    run([*compose, "stop"], "stop.log", timeout=240)
    report = {
        "passed": True,
        "resumed_after_verified_startup": args.resume_after_startup,
        "project": args.project,
        "fresh_web_port": args.port,
        "fresh_application_volumes": True,
        "new_credentials": True,
        "model_cache_copies": cache_copies,
        "model_bootstrap_pin_probe": "PASS",
        "operational_episodes": len(after["operational_views"]),
        "snapshots_preserved": len(after["snapshots"]),
        "saved_results_preserved": len(after["saved_result_hashes"]),
        "operational_proofs_opened_each_side": sum(
            value["proofs_opened"] for value in after["operational_views"].values()
        ),
        "restore": json.loads((destination / "restore.json").read_bytes()),
        "runtime_images": {
            item["Config"]["Labels"]["com.docker.compose.service"]: item["Image"]
            for item in containers
        },
        "drill_containers_stopped": True,
        "other_projects_modified": False,
        "diagnostic_ports": [args.pg_port, args.bolt_port, args.restore_bolt_port],
    }
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
