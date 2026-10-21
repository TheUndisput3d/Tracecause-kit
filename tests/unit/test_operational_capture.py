"""Capture boundaries independent of databases and model providers."""

import argparse
import json
from pathlib import Path

import pytest
from scripts.capture.operational_lab import APPLICATIONS, RELEASES, free_port, sha
from scripts.capture.operational_lab_runtime import Runtime


def test_release_artifacts_have_distinct_bytes_and_real_implementations(tmp_path: Path) -> None:
    import runpy

    assert len({sha(value.encode()) for value in RELEASES.values()}) == 3
    for version, text in RELEASES.items():
        path = tmp_path / f"{version}.py"
        path.write_text(text)
        release = runpy.run_path(str(path))
        assert release["VERSION"] == version
        assert release["accept"](2) is True
        assert release["accept"](1) is (version != "1.1.0")


def test_capture_has_three_distinct_topologies_and_namespaces() -> None:
    assert len({spec["topology"] for spec in APPLICATIONS}) == 3
    assert len({spec["id"] for spec in APPLICATIONS}) == 3
    for spec in APPLICATIONS:
        assert spec["entry"] in spec["roles"]
        assert spec["target"] in spec["roles"]
        assert spec["target"] != spec["entry"]


def test_ephemeral_port_allocator_does_not_reuse_live_lab_reservations() -> None:
    ports = [free_port() for _ in range(20)]
    assert len(set(ports)) == 20
    assert all(1024 < port < 65536 for port in ports)


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com",
        "http://localhost:1",
        "http://10.0.0.1:1",
        "http://127.0.0.1:80@example.com",
        "http://127.0.0.1:80/path",
    ],
)
def test_runtime_dependency_boundary_requires_explicit_loopback(
    tmp_path: Path, target: str
) -> None:
    release = tmp_path / "release.py"
    release.write_text(RELEASES["1.0.0"])
    runtime = Runtime(
        argparse.Namespace(release_file=release, dependencies=json.dumps({"x": target}))
    )
    with pytest.raises(ValueError, match="non-loopback"):
        runtime.request("x", "/work", "a" * 32, "b" * 16, {})
    with pytest.raises(ValueError, match="outside the local lab"):
        runtime.request("other", "/work", "a" * 32, "b" * 16, {})
