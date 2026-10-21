"""Compare every persisted span column across two measured import databases."""

import argparse
import hashlib
import json
from pathlib import Path

from psycopg import connect


def digest(database: str) -> dict[str, object]:
    if not database.startswith("tracecause_perf_"):
        raise ValueError("only isolated performance databases are allowed")
    password = Path(".secrets/postgres_admin_password").read_text().strip()
    with connect(
        host="127.0.0.1", port=15432, dbname=database, user="postgres", password=password
    ) as connection:
        count = connection.execute("SELECT count(*) FROM spans").fetchone()[0]
        hashed = hashlib.sha256()
        with connection.cursor().copy(
            "COPY (SELECT row_to_json(s)::text FROM spans s "
            "ORDER BY episode_id, trace_id, span_id) TO STDOUT"
        ) as copied:
            for block in copied:
                hashed.update(block)
    return {"database": database, "rows": count, "all_columns_sha256": hashed.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("optimized", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        digest(json.loads(path.read_text())["database"]) for path in (args.baseline, args.optimized)
    ]
    equal = (
        rows[0]["rows"] == rows[1]["rows"] == 100000
        and rows[0]["all_columns_sha256"] == rows[1]["all_columns_sha256"]
    )
    report = {"databases": rows, "all_stored_span_columns_equal": equal}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if not equal:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
