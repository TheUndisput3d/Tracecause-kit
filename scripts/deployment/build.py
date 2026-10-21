"""Build never-overwritten local release images from an archived source checkout."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,70}", args.version):
        raise ValueError("release version must be a short lowercase image tag")
    tags = {name: f"tracecause-{name}:{args.version}" for name in ("backend", "web")}
    for tag in tags.values():
        found = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
        if found.returncode == 0:
            raise ValueError("image tag already exists; choose a new release version")
    output = Path("reports/releases") / args.version
    output.mkdir(parents=True)
    source = output / "source"
    source.mkdir()
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).split(b"\0")
    entries = []
    for name in sorted(set(names)):
        if not name:
            continue
        path = Path(os.fsdecode(name))
        if path.is_symlink():
            raise ValueError("release source cannot contain symlinks")
        if path.is_file():
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            entries.append(
                {"path": str(path), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            )
    source_hash = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    pins = dict(
        line.split("=", 1)
        for line in Path(".env.release").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    manifest = {
        "version": args.version,
        "git_head": head,
        "source_sha256": source_hash,
        "files": entries,
        "images": {},
    }
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name, variables in (
        ("backend", ("PYTHON_IMAGE", "UV_IMAGE")),
        ("web", ("NODE_IMAGE", "CADDY_IMAGE")),
    ):
        command = [
            "docker",
            "build",
            "--file",
            str(source / f"Dockerfile.{name}"),
            "--tag",
            tags[name],
        ]
        for key in variables:
            if "@sha256:" not in pins[key]:
                raise ValueError("base image must use an explicit digest")
            command += ["--build-arg", f"{key}={pins[key]}"]
        command += [
            "--build-arg",
            f"VCS_REF={head}+source.{source_hash}",
            "--build-arg",
            f"RELEASE_VERSION={args.version}",
            str(source),
        ]
        with (output / f"{name}-build.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
        item = json.loads(subprocess.check_output(["docker", "image", "inspect", tags[name]]))[0]
        manifest["images"][name] = {
            "tag": tags[name],
            "image_id": item["Id"],
            "labels": item["Config"]["Labels"],
            "user": item["Config"]["User"],
            "size_bytes": item["Size"],
        }
        if item["Config"]["User"] != "10001:10001":
            raise ValueError("application image does not use the required non-root UID")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    updates = {
        "APP_IMAGE": manifest["images"]["backend"]["image_id"],
        "WEB_IMAGE": manifest["images"]["web"]["image_id"],
        "VCS_REF": head + "+source." + source_hash,
        "RELEASE_VERSION": args.version,
    }
    path = Path(".env.release")
    path.write_text(
        "\n".join(key + "=" + str(updates.get(key, value)) for key, value in pins.items()) + "\n"
    )
    print(
        json.dumps(
            {"manifest": str(output / "manifest.json"), "images": manifest["images"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
