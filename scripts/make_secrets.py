"""Create local development secrets without printing them."""

from pathlib import Path
from secrets import token_urlsafe


def main() -> None:
    root = Path(__file__).resolve().parents[1] / ".secrets"
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for name in (
        "postgres_admin_password",
        "postgres_password",
        "operator_token",
        "capture_control_token",
    ):
        path = root / name
        if not path.exists():
            path.write_text(token_urlsafe(36), encoding="utf-8")
        # The official PostgreSQL entrypoint runs init scripts as its service
        # user. Compose bind mounts retain host file modes, so this one file
        # must be readable from that uid; the host parent stays 0700.
        path.chmod(0o644 if name in {"postgres_password", "capture_control_token"} else 0o600)
    neo4j = root / "neo4j_auth"
    if not neo4j.exists():
        neo4j.write_text("neo4j/" + token_urlsafe(36), encoding="utf-8")
    # Neo4j's image drops root before reading its file-backed secret. The
    # containing host directory remains 0700, while Docker bind-mounts this
    # file readable to the container's unprivileged user.
    neo4j.chmod(0o644)


if __name__ == "__main__":
    main()
