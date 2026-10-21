"""Run the local-only real API, worker and Vite verification processes."""

import os
import shutil
import signal
import subprocess
import sys
import time

from scripts.dashboard.seed import OUTPUT, ROOT, environment


def main() -> None:
    env = {**os.environ, **environment(), "TRACECAUSE_API_TARGET": "http://127.0.0.1:18080"}
    if os.environ.get("TRACECAUSE_DASHBOARD_LLM") == "1":
        from tracecause.investigation.generation import PINNED_MANIFEST_DIGEST

        env.update(
            {
                "TRACECAUSE_ENABLE_LOCAL_MODEL": "true",
                "TRACECAUSE_GENERATION_PROVIDER": "ollama",
                "TRACECAUSE_OLLAMA_URL": "http://127.0.0.1:11434",
                "TRACECAUSE_OLLAMA_MODEL_DIGEST": PINNED_MANIFEST_DIGEST,
            }
        )
    env["PATH"] = str(ROOT / ".tools/node_modules/node/bin") + os.pathsep + env["PATH"]
    pinned_node = ROOT / ".tools/node_modules/node/bin/node"
    node = str(pinned_node) if pinned_node.is_file() else shutil.which("node")
    if node is None:
        raise RuntimeError("Node24 is required for the dashboard")
    commands = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "tracecause.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18080",
            "--no-access-log",
        ],
        "worker": [sys.executable, "-m", "tracecause.jobs.api_worker"],
        "web": [
            node,
            "apps/web/node_modules/vite/bin/vite.js",
            "apps/web",
            "--host",
            "127.0.0.1",
            "--port",
            "15173",
            "--strictPort",
        ],
    }
    processes = []
    logs = []
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for name, command in commands.items():
            log = (OUTPUT / f"{name}.log").open("a")
            logs.append(log)
            processes.append(
                subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            )
        while not stopping:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("a dashboard verification process exited")
            time.sleep(0.5)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
