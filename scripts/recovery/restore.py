"""Restore a trusted backup into a NEW local database, artifact root and empty graph.

Never restore SQL from an untrusted party. This operator tool refuses an existing
database or artifact directory. Settings must point at the isolated target graph.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from psycopg import connect, sql

from scripts.recovery.backup import file_hash
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.graph.recovery import rebuild_empty_graph
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.maintenance import backup_metadata


def restore(backup: Path, database: str, container: str) -> dict[str, object]:
    if not re.fullmatch(r"tracecause_restore_[a-z0-9_]{1,40}", database):
        raise ValueError("target requires a new tracecause_restore_ database name")
    settings = load_settings(Path("configs/defaults.yaml"))
    if settings.pg_user != "postgres" or settings.pg_password is None:
        raise ValueError("restore requires the explicit local database administrator")
    if settings.artifact_root.exists():
        raise ValueError("restore artifact root must be new")
    manifest = json.loads((backup / "manifest.json").read_bytes())
    if manifest["format"] != "tracecause-backup-v1":
        raise ValueError("unsupported backup format")
    if file_hash(backup / "postgres.dump") != manifest["dump_sha256"]:
        raise ValueError("database dump hash mismatch")
    source = ArtifactRepository((backup / "artifacts").absolute())
    for item in manifest["artifacts"]:
        if source.verify(item["sha256"]) != item["bytes"]:
            raise ValueError("backup artifact size mismatch")
    with connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname="postgres",
        user="postgres",
        password=settings.pg_password.get_secret_value(),
        autocommit=True,
    ) as connection:
        # CREATE DATABASE fails if already present; no drop/overwrite path exists.
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    with (backup / "postgres.dump").open("rb") as dump:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                "postgres",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "-d",
                database,
            ],
            stdin=dump,
            capture_output=True,
            check=True,
            timeout=180,
        )
    target = ArtifactRepository(settings.artifact_root)
    for item in manifest["artifacts"]:
        digest = item["sha256"]
        with (source.root / "sha256" / digest[:2] / digest).open("rb") as data:
            restored = target.put(data)
        if restored.content_hash != digest or target.verify(digest) != item["bytes"]:
            raise ValueError("restored artifact hash mismatch")
    resources = open_resources(settings.model_copy(update={"pg_database": database}))
    try:
        with resources.factory() as session:
            observed = backup_metadata(session)
        expected = manifest["metadata"]
        if any(observed[key] != expected[key] for key in ("schema_revision", "row_counts")):
            raise ValueError("restored database counts or schema differ")
        snapshots = rebuild_empty_graph(resources)
        result = {
            "database": database,
            "schema_revision": observed["schema_revision"],
            "row_counts": observed["row_counts"],
            "artifacts_verified": len(manifest["artifacts"]),
            "snapshots_rebuilt": snapshots,
            "dump_sha256": manifest["dump_sha256"],
        }
        return result
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--postgres-container", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = restore(args.backup, args.database, args.postgres_container)
    with args.report.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
