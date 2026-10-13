"""Stop and recover only the owned restore graph; exercise real readiness checks."""

import json
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.dashboard.seed import environment
from tracecause.api.main import create_app
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings

CONTAINER = "tracecause-recovery-v1-neo4j-1"


def main() -> None:
    inspected = json.loads(
        subprocess.run(
            ["docker", "inspect", CONTAINER], check=True, capture_output=True, text=True
        ).stdout
    )[0]
    assert inspected["Config"]["Labels"]["com.docker.compose.project"] == "tracecause-recovery-v1"
    config = environment(admin=True)
    config.update(
        {
            "TRACECAUSE_PG_DATABASE": "tracecause_restore_phase16_v1",
            "TRACECAUSE_NEO4J_URI": "bolt://127.0.0.1:27687",
            "TRACECAUSE_ARTIFACT_ROOT": str(
                Path("reports/phase16-restored-artifacts-v1").absolute()
            ),
            "TRACECAUSE_PUBLIC_READ_ONLY": "true",
        }
    )
    resources = open_resources(load_settings(Path("configs/defaults.yaml"), config))
    try:
        with TestClient(
            create_app(resources.settings, resources), raise_server_exceptions=False
        ) as client:
            before = client.get("/health/ready")
            assert before.status_code == 200, before.text
            try:
                subprocess.run(
                    ["docker", "stop", CONTAINER], check=True, capture_output=True, timeout=45
                )
                live = client.get("/health/live")
                failed = client.get("/health/ready")
                assert live.status_code == 200
                assert failed.status_code == 503 and failed.json()["error"]["retryable"]
                assert "bolt://" not in failed.text and "password" not in failed.text
            finally:
                subprocess.run(
                    ["docker", "start", CONTAINER], check=True, capture_output=True, timeout=15
                )
            started = time.monotonic()
            for _ in range(60):
                after = client.get("/health/ready")
                if after.status_code == 200:
                    break
                time.sleep(1)
            assert after.status_code == 200, after.text
            report = {
                "dependency": "actual isolated Neo4j container",
                "before": before.status_code,
                "during": failed.status_code,
                "liveness_during": live.status_code,
                "safe_error": failed.json()["error"]["code"],
                "after": after.status_code,
                "recovery_seconds": round(time.monotonic() - started, 3),
            }
            Path("reports/phase16-outage.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    finally:
        resources.close()


if __name__ == "__main__":
    main()
