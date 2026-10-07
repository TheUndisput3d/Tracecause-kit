"""Pinned dependency and tracked-source secret scans with retained raw reports."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_SHA = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/phase17-scans"))
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    tools = Path(".tools/scanners")
    tools.mkdir(parents=True, exist_ok=True)
    binary = tools / "gitleaks"
    archive = tools / f"gitleaks-{GITLEAKS_VERSION}.tar.gz"
    if not archive.exists():
        url = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/gitleaks_{GITLEAKS_VERSION}_linux_x64.tar.gz"
        with urllib.request.urlopen(url, timeout=30) as source:
            archive.write_bytes(source.read(30 * 1024 * 1024))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != GITLEAKS_SHA:
        raise ValueError("secret scanner release hash mismatch")
    with tarfile.open(archive) as release:
        source = release.extractfile("gitleaks")
        if source is None:
            raise ValueError("scanner binary absent")
        binary.write_bytes(source.read())
        binary.chmod(0o755)
    packages = tomllib.loads(Path("uv.lock").read_text())["package"]
    requirements = output / "locked-requirements.txt"
    requirements.write_text(
        "".join(
            f"{item['name']}=={item['version'].split('+')[0]}\n"
            for item in packages
            if item["name"] != "tracecause"
        )
    )
    commands = {
        "python": [
            "uv",
            "tool",
            "run",
            "--from",
            "pip-audit==2.10.1",
            "pip-audit",
            "--no-deps",
            "--disable-pip",
            "-r",
            str(requirements),
            "-f",
            "json",
            "-o",
            str(output / "python-audit.json"),
        ],
        "npm": ["npm", "--prefix", "apps/web", "audit", "--json"],
        "secrets": [
            str(binary),
            "git",
            "--redact",
            "--no-banner",
            "--report-format",
            "json",
            "--report-path",
            str(output / "gitleaks.json"),
            ".",
        ],
    }
    tree = tempfile.TemporaryDirectory(prefix="tracecause-scan-")
    with tree as directory:
        files = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=True,
            capture_output=True,
        ).stdout.split(b"\x00")
        for name in sorted(set(files)):
            if not name:
                continue
            source = Path(os.fsdecode(name))
            if source.is_symlink():
                raise ValueError("source scan refuses symlinks")
            if source.is_file():
                target = Path(directory) / source
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        command = [
            str(binary),
            "dir",
            "--redact",
            "--no-banner",
            "--config",
            ".gitleaks.toml",
            "--report-format",
            "json",
            "--report-path",
            str(output / "working-tree.json"),
            directory,
        ]
        with (output / "working-tree.log").open("w") as log:
            tree_exit = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=120
            ).returncode
    report = {
        "versions": {"pip-audit": "2.10.1", "gitleaks": GITLEAKS_VERSION},
        "python_local_version_policy": (
            "strip wheel local suffix for advisory lookup; no dependency resolution"
        ),
        "results": {"working_tree": {"exit_code": tree_exit}},
    }
    for name, command in commands.items():
        with (output / f"{name}.log").open("w") as log:
            completed = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy(), timeout=300
            )
        report["results"][name] = {"exit_code": completed.returncode}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if any(item["exit_code"] for item in report["results"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
