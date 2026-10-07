"""Own the actual local API/worker/Vite lifecycle for a bounded browser CI gate."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/ci-browser"))
    args = parser.parse_args()
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TRACECAUSE_BROWSER_ARTIFACTS": str(args.output)}
    # Normal CI is template-only. Explicit local-model verification uses its own launcher.
    env.pop("TRACECAUSE_DASHBOARD_LLM", None)
    subprocess.run(
        [sys.executable, "-m", "scripts.dashboard.seed"], check=True, env=env, timeout=180
    )
    with (output / "launcher.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "scripts.dashboard.serve"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            subprocess.run(
                ["npm", "--prefix", "apps/web", "run", "test:e2e"], check=True, env=env, timeout=300
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
