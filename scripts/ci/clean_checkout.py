"""Copy the current source into a new local checkout without secrets or caches.

The original commit history is retained for scanner review fingerprints. This
performs no network Git operation and does not push or modify the original tree.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.absolute()
    if destination.exists():
        raise ValueError("clean checkout destination must be new")
    subprocess.run(
        ["git", "clone", "--no-hardlinks", "--no-checkout", str(Path.cwd()), str(destination)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "reset", "--mixed", "HEAD"], check=True, capture_output=True
    )
    names = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\x00")
    manifest = []
    for name in sorted(set(names)):
        if not name:
            continue
        path = Path(os.fsdecode(name))
        if path.is_symlink():
            raise ValueError("clean source copy refuses symlinks")
        if path.is_file():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            manifest.append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
    report = {
        "files": manifest,
        "source_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "worktree_changes_included": True,
        "runtime_environment": "not installed yet",
        "copy_excludes": (
            "gitignored secrets, model caches, generated fixtures, reports and dependencies"
        ),
    }
    (destination.parent / "source-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"destination": str(destination), "source_files": len(manifest)}))


if __name__ == "__main__":
    main()
