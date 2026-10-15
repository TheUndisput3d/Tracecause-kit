"""Fail when committed TypeScript API types drift from the OpenAPI export."""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pinned_node = ROOT / ".tools/node_modules/node/bin/node"
    node = str(pinned_node) if pinned_node.is_file() else shutil.which("node")
    if node is None:
        raise RuntimeError("Node24 is required to verify frontend types")
    cli = ROOT / "apps/web/node_modules/openapi-typescript/bin/cli.js"
    source = ROOT / "contracts/openapi.phase01.json"
    committed = ROOT / "apps/web/src/lib/api/generated.ts"
    with tempfile.TemporaryDirectory(prefix="tracecause-types-") as directory:
        output = Path(directory) / "generated.ts"
        subprocess.run(
            [str(node), str(cli), str(source), "-o", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        if output.read_bytes() != committed.read_bytes():
            print("Frontend API types are out of date; run make contract")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
