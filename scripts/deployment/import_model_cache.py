"""Copy only verified pinned Qwen blobs from a local cache into the owned release volume."""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

from tracecause.investigation.generation import PINNED_MANIFEST_DIGEST


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = Path("manifests/registry.ollama.ai/library/qwen3/4b-instruct-2507-q4_K_M")
    raw = (args.source / manifest_path).read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != PINNED_MANIFEST_DIGEST:
        raise ValueError("source model manifest differs from pinned identity")
    manifest = json.loads(raw)
    paths = [manifest_path]
    for item in (manifest["config"], *manifest["layers"]):
        path = Path("blobs") / item["digest"].replace(":", "-")
        source = args.source / path
        if source.is_symlink() or source.stat().st_size != item["size"]:
            raise ValueError("model blob is not a regular file of expected size")
        with source.open("rb") as file:
            if "sha256:" + hashlib.file_digest(file, "sha256").hexdigest() != item["digest"]:
                raise ValueError("model blob digest mismatch")
        paths.append(path)
    compose = [
        "docker",
        "compose",
        "--env-file",
        ".env.release",
        "-f",
        "compose.release.yaml",
        "--profile",
        "llm",
    ]
    identifier = subprocess.check_output([*compose, "ps", "-q", "ollama"], text=True).strip()
    if not identifier:
        raise ValueError("owned release Ollama must be running")
    info = json.loads(subprocess.check_output(["docker", "inspect", identifier]))[0]
    if info["Config"]["Labels"].get("com.docker.compose.project") != "tracecause-release":
        raise ValueError("refuse a provider outside the selected release project")
    subprocess.run(
        ["docker", "exec", identifier, "mkdir", "-p", "/root/.ollama/models"], check=True
    )
    existing = subprocess.check_output(
        ["docker", "exec", identifier, "find", "/root/.ollama/models", "-type", "f"]
    )
    if existing.strip():
        raise ValueError("destination model store must be empty")
    process = subprocess.Popen(
        ["docker", "cp", "-", identifier + ":/root/.ollama/models"], stdin=subprocess.PIPE
    )
    assert process.stdin is not None
    with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
        for path in paths:
            archive.add(args.source / path, arcname=str(path), recursive=False)
    process.stdin.close()
    if process.wait(timeout=60) != 0:
        raise RuntimeError("model cache copy failed")
    report = {
        "manifest_digest": PINNED_MANIFEST_DIGEST,
        "verified_files": len(paths),
        "source_modified": False,
        "destination": "release named Ollama volume",
    }
    with args.report.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
