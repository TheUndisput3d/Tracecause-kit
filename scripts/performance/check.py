"""Run explicit performance diagnostics and compare them with recorded tolerances.

Requires the prepared local dashboard and database administrator secret. This
never runs implicitly in ordinary pull requests and never calls a model provider.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.dashboard.seed import environment


def main() -> None:
    output = Path("reports/performance") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    budgets = json.loads(Path("configs/performance-budgets.json").read_text())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.performance.reference",
            "--output",
            str(output / "reference"),
        ],
        check=True,
        timeout=180,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.performance.import_benchmark",
            "--output",
            str(output / "import"),
            "--spans",
            "100000",
            "--run-id",
            "0",
        ],
        check=True,
        env={**os.environ, **environment(admin=True)},
        timeout=600,
    )
    reference = json.loads((output / "reference/report.json").read_text())
    imported = json.loads((output / "import/report.json").read_text())
    checks = {
        "warm_investigations": all(
            v["p95_nearest_rank"] <= budgets["warm_investigation_p95_ms"]
            for v in reference["warm_investigation_ms"].values()
        ),
        "metadata": reference["http"]["metadata"]["milliseconds"]["p95_nearest_rank"]
        <= budgets["metadata_p95_ms"],
        "graph": reference["http"]["graph"]["milliseconds"]["p95_nearest_rank"]
        <= budgets["graph_p95_ms"],
        "import_elapsed": imported["timings_seconds"]["total_import"]
        <= budgets["import_100k_seconds"],
        "import_memory": imported["peak_process_rss_kib"] <= budgets["import_peak_rss_kib"],
        "import_queries": imported["sql_statements"]["total_including_verification"]
        <= budgets["import_sql_statements"],
        "no_errors": reference["errors"] == 0 and imported["errors"] == 0,
        "no_provider_calls": reference["provider_calls"] == 0,
    }
    report = {"checks": checks, "passed": all(checks.values()), "budgets": budgets}
    (output / "budget-result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), **report}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
