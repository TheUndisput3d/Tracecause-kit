"""Static exposure configuration and built-asset secret checks, not a network scan."""

import json
import subprocess
from pathlib import Path


def main() -> None:
    root = Path.cwd()
    config = json.loads(
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "-f",
                "compose.internal.yaml",
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for name in ("postgres", "neo4j"):
        assert not config["services"][name].get("ports")
        assert set(config["services"][name]["networks"]) == {"data_net"}
    assert config["networks"]["data_net"]["internal"] is True
    secrets = []
    for path in (root / ".secrets").iterdir():
        if path.is_file() and not path.is_symlink():
            value = path.read_bytes().strip()
            if len(value) >= 8:
                secrets.append(value)
                if path.name == "neo4j_auth":
                    secrets.append(value.split(b"/", 1)[1])
    operator = root / "reports/phase15-dashboard/operator_token"
    secrets.append(operator.read_bytes().strip())
    assets = [p for p in (root / "apps/web/dist").rglob("*") if p.is_file()]
    assert assets, "build frontend before scanning"
    for path in assets:
        raw = path.read_bytes()
        if any(value in raw for value in secrets):
            raise ValueError("configured secret found in built frontend")
    report = {
        "internal_database_config": True,
        "network_scan": False,
        "frontend_assets_scanned": len(assets),
        "configured_secret_matches": 0,
        "public_deployment": "not deployed",
    }
    (root / "reports/phase16-static-boundaries.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
