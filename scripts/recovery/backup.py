"""Create a verified PostgreSQL/artifact backup with source membership metadata.

Use the normal TRACECAUSE_* settings and stop writers first. PostgreSQL SHARE
locks additionally prevent concurrent writes while pg_dump and copies run.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from sqlalchemy import func, select, text

from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.graph.recovery import backfill_membership
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.maintenance import backup_metadata
from tracecause.storage.schema import Base, JobRow, SourceArtifactRow


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def backup(output: Path, container: str) -> dict[str, object]:
    if output.exists() or output.is_symlink():
        raise ValueError("backup destination must be new")
    settings = load_settings(Path("configs/defaults.yaml"))
    if not re.fullmatch(r"[a-zA-Z0-9_]+", settings.pg_database):
        raise ValueError("invalid database name")
    resources = open_resources(settings)
    output.mkdir(parents=True, mode=0o700)
    target = ArtifactRepository((output / "artifacts").absolute())
    try:
        backfilled = backfill_membership(resources)
        with resources.factory.begin() as session:
            # Names come exclusively from our static SQLAlchemy metadata.
            tables = sorted(Base.metadata.tables)
            session.execute(text("LOCK TABLE " + ", ".join(tables) + " IN SHARE MODE"))
            pending = session.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT")))
            )
            if pending:
                raise ValueError(
                    "drain or cancel pending jobs before backup; staging is not backed up"
                )
            metadata = backup_metadata(session)
            dump_path = output / "postgres.dump"
            with dump_path.open("xb") as dump:
                os.chmod(dump_path, 0o600)
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        container,
                        "pg_dump",
                        "-U",
                        "postgres",
                        "-Fc",
                        "--no-owner",
                        "--no-acl",
                        settings.pg_database,
                    ],
                    stdout=dump,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=180,
                )
                dump.flush()
                os.fsync(dump.fileno())
            artifacts = []
            for row in session.scalars(select(SourceArtifactRow)).all():
                size = resources.artifacts.verify(row.content_hash)
                source = (
                    resources.artifacts.root / "sha256" / row.content_hash[:2] / row.content_hash
                )
                with source.open("rb") as data:
                    copied = target.put(data)
                if copied.content_hash != row.content_hash or copied.byte_size != size:
                    raise ValueError("backup artifact verification failed")
                artifacts.append({"sha256": copied.content_hash, "bytes": size})
            manifest = {
                "format": "tracecause-backup-v1",
                "database": settings.pg_database,
                "metadata": metadata,
                "membership_backfilled": backfilled,
                "dump_sha256": file_hash(dump_path),
                "artifacts": sorted(artifacts, key=lambda item: str(item["sha256"])),
            }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--postgres-container", required=True)
    args = parser.parse_args()
    report = backup(args.output, args.postgres_container)
    print(json.dumps({"output": str(args.output), "artifacts": len(report["artifacts"])}))


if __name__ == "__main__":
    main()
