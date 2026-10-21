"""Static release boundaries supplement (not replace) actual container drills."""

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_release_database_and_operator_boundaries() -> None:
    config = yaml.safe_load((ROOT / "compose.release.yaml").read_text())
    services = config["services"]
    assert config["networks"]["data_net"]["internal"] is True
    for name in ("postgres", "neo4j"):
        assert "ports" not in services[name]
        assert services[name]["networks"] == ["data_net"]
    for name in ("api", "worker"):
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "postgres_admin_password" not in service["secrets"]
        assert service["mem_limit"] and service["cpus"]
        assert service["logging"]["options"]["max-file"] == "3"
    assert services["worker"]["profiles"] == ["operator"]
    assert services["ollama"]["profiles"] == ["llm"]
    assert services["api"]["environment"]["TRACECAUSE_PUBLIC_READ_ONLY"].endswith(":-true}")
    assert "artifacts:/var/lib/tracecause/artifacts:ro" in services["api"]["volumes"]
    assert all(port.startswith("127.0.0.1:") for port in services["web"]["ports"])


def test_images_exclude_model_downloads_and_include_documentation() -> None:
    backend = (ROOT / "Dockerfile.backend").read_text()
    assert "snapshot_download" not in backend and "SentenceTransformer(" not in backend
    assert "model_cache" not in backend and "COPY . " not in backend
    assert "USER 10001:10001" in backend
    web = (ROOT / "Dockerfile.web").read_text()
    assert "COPY docs /app/docs" in web and "WORKDIR /app/apps/web" in web
    assert "USER 10001:10001" in web
    assert "setcap -r /usr/bin/caddy" in web
    assert "data/gold" in (ROOT / ".dockerignore").read_text()
    for excluded in ("reports", "data/captured_private", "data/generated", ".env.release"):
        assert excluded in (ROOT / ".dockerignore").read_text()


def test_container_seed_uses_executable_package_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tracecause", "--help"], check=True, capture_output=True, text=True
    )
    assert "investigation" in result.stdout and "models" in result.stdout
    source = (ROOT / "src/tracecause/operations.py").read_text()
    assert '"tracecause.cli"' not in source


def test_cli_default_retains_heuristic_after_negative_captured_transfer() -> None:
    import argparse

    from tracecause.cli_investigation import add_investigation_commands

    parser = argparse.ArgumentParser()
    add_investigation_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "investigation",
            "run",
            "--episode-id",
            "03c9ef66-2609-5aec-86fa-9dda41e1c9af",
            "--artifact-root",
            "/tmp/example-artifacts",
            "--password-file",
            ".secrets/postgres_password",
            "--neo4j-auth-file",
            ".secrets/neo4j_auth",
        ]
    )
    assert args.ranking_mode == "heuristic"


def test_integration_target_prepares_generated_fixtures_before_api_tests() -> None:
    assert "test-integration: fixtures" in (ROOT / "Makefile").read_text()
