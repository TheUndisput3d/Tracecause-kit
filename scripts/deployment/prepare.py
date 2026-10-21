"""Prepare local release configuration and distinct file secrets, without printing secrets."""

import hashlib
import json
import secrets
import subprocess
from pathlib import Path


def main() -> None:
    folder = Path(".secrets/release")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    for name in ("postgres_admin_password", "postgres_password", "neo4j_auth", "operator_token"):
        path = folder / name
        if path.is_symlink():
            raise ValueError("release secret must not be a symlink")
        if not path.exists():
            with path.open("x") as output:
                output.write(
                    ("neo4j/" if name == "neo4j_auth" else "") + secrets.token_urlsafe(36) + "\n"
                )
        # Docker bind-backed secrets retain host mode. The host parent remains0700;
        # only explicitly mounted files are readable by application UID10001.
        path.chmod(0o444)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"])
    revision = head + ("-worktree-" + hashlib.sha256(diff).hexdigest()[:12] if diff else "")
    target = Path(".env.release")
    if not target.exists():
        target.write_text(
            Path(".env.example").read_text() + f"\nAPP_IMAGE=tracecause-backend:local-{head[:12]}\n"
            f"WEB_IMAGE=tracecause-web:local-{head[:12]}\nVCS_REF={revision}\n"
            "RELEASE_VERSION=local\nPUBLIC_READ_ONLY=true\nLOCAL_WEB_PORT=13000\n"
        )
    print(json.dumps({"configuration": str(target), "secrets": "prepared, values not printed"}))


if __name__ == "__main__":
    main()
