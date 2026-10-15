"""Initialize only named Docker volumes; retain credentials across every restart."""

import os
import secrets
import tempfile
from pathlib import Path


def secret(directory: Path, name: str, prefix: str = "") -> None:
    path = directory / name
    if path.is_symlink():
        raise ValueError("secret path must not be a symlink")
    if path.exists():
        value = path.read_text().strip()
        if len(value) < 32 or (prefix and not value.startswith(prefix)):
            raise ValueError("existing secret is invalid; refusing credential rotation")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".new-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(prefix + secrets.token_urlsafe(36) + "\n")
            output.flush()
            os.fchmod(output.fileno(), 0o444)
            os.fsync(output.fileno())
        os.link(temporary, path)  # Atomic publication; never replace an existing credential.
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    app = Path("/run/secrets")
    admin = Path("/run/admin-secrets")
    for folder in (app, admin):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o755)
    for name in ("postgres_password", "operator_token"):
        secret(app, name)
    secret(app, "neo4j_auth", "neo4j/")
    secret(admin, "postgres_admin_password")
    for folder in (
        Path("/var/lib/tracecause/artifacts"),
        Path("/var/lib/tracecause/staging"),
        Path("/var/cache/huggingface"),
    ):
        folder.mkdir(parents=True, exist_ok=True)
        os.chown(folder, 10001, 10001)
    print("Persistent credentials and application volume ownership initialized.")


if __name__ == "__main__":
    main()
