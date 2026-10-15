"""Run the Phase 00 driver probe with local development secret files."""

import os
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    secrets = ROOT / ".secrets"
    user, neo4j_password = (secrets / "neo4j_auth").read_text(encoding="utf-8").split("/", 1)
    postgres_password = (secrets / "postgres_password").read_text(encoding="utf-8")
    env = os.environ.copy()
    env.update(
        TRACECAUSE_PG_DSN=(
            "postgresql://tracecause_app:"
            + quote(postgres_password, safe="")
            + "@127.0.0.1:15432/tracecause"
        ),
        TRACECAUSE_NEO4J_URI="bolt://127.0.0.1:17687",
        TRACECAUSE_NEO4J_USER=user,
        TRACECAUSE_NEO4J_PASSWORD=neo4j_password,
    )
    return subprocess.call(
        ["uv", "run", "--locked", "tracecause", "doctor", "--check-databases"], env=env
    )


if __name__ == "__main__":
    raise SystemExit(main())
