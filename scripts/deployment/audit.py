"""Inspect running release container boundaries and copied frontend assets."""

import argparse
import json
import subprocess
from pathlib import Path

from scripts.deployment.backup import COMPOSE, container


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    rows = []
    for name in ("postgres", "neo4j", "api", "web"):
        item = container(name)
        ports = item["HostConfig"]["PortBindings"] or {}
        if name != "web":
            assert not ports
        else:
            assert all(
                binding["HostIp"] == "127.0.0.1" for values in ports.values() for binding in values
            )
        if name in {"api", "web"}:
            assert item["Config"]["User"] == "10001:10001"
            assert item["HostConfig"]["ReadonlyRootfs"]
            assert item["HostConfig"]["CapDrop"] == ["ALL"]
        if name == "api":
            artifact = [
                m for m in item["Mounts"] if m["Destination"] == "/var/lib/tracecause/artifacts"
            ]
            assert len(artifact) == 1 and artifact[0]["RW"] is False
            assert all(
                m["Destination"] != "/run/secrets/postgres_admin_password" for m in item["Mounts"]
            )
        assert item["State"]["Health"]["Status"] == "healthy"
        rows.append(
            {
                "service": name,
                "image_id": item["Image"],
                "user": item["Config"]["User"],
                "ports": ports,
                "memory_limit": item["HostConfig"]["Memory"],
                "cpu_limit": item["HostConfig"]["NanoCpus"],
                "health": "healthy",
            }
        )
    api = container("api")
    probe = """import os,json,sys
from pathlib import Path
assert os.getuid()==10001
assert not Path('/app/.secrets').exists()
assert not Path('/app/data/gold').exists()
assert not Path('/var/cache/huggingface').exists()
print(json.dumps({'uid':os.getuid(),'python':sys.version.split()[0],
    'model_cache_baked':False,'gold_baked':False,'secret_directory_baked':False}))
"""
    image_probe = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "python",
                api["Image"],
                "-c",
                probe,
            ]
        )
    )
    subprocess.run([*COMPOSE, "cp", "web:/srv", str(args.output / "web-assets")], check=True)
    values = [
        path.read_bytes().strip() for path in Path(".secrets/release").iterdir() if path.is_file()
    ]
    values += [value.split(b"/", 1)[1] for value in values if value.startswith(b"neo4j/")]
    files = [p for p in (args.output / "web-assets").rglob("*") if p.is_file()]
    assert files
    for path in files:
        if any(value in path.read_bytes() for value in values):
            raise ValueError("release secret appears in frontend assets")
    report = {
        "containers": rows,
        "image_probe": image_probe,
        "frontend_files_scanned": len(files),
        "secret_matches": 0,
        "external_network_scan": False,
        "public_deployment": False,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
