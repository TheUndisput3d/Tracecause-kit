"""CLI acceptance, partial import visibility and artifact path confinement."""

import hashlib
import json
import shutil
from pathlib import Path

from tracecause.cli import main


def _copy_episode(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2] / "data/fixtures/smoke"
    episode_id = json.loads((root / "index.json").read_text())["episodes"][0]["episode_id"]
    target = tmp_path / "episode"
    shutil.copytree(root / episode_id, target)
    return target


def test_cli_accepts_smoke_and_writes_redacted_artifact(tmp_path: Path) -> None:
    episode = _copy_episode(tmp_path)
    root = tmp_path / "artifacts"
    assert (
        main(
            [
                "normalize",
                "--episode-dir",
                str(episode),
                "--artifact-root",
                str(root),
                "--output-dir",
                "smoke",
            ]
        )
        == 0
    )
    report = json.loads((root / "smoke/report.json").read_text())
    assert report["status"] == "accepted"
    assert report["report"]["accepted_count"] == 71
    assert report["report"]["duplicate_count"] == 1
    assert len((root / "smoke/spans.jsonl").read_text().splitlines()) == 71
    assert (
        hashlib.sha256((root / "smoke/spans.jsonl").read_bytes()).hexdigest()
        == report["redacted_hash"]
    )


def test_cli_strict_failure_and_partial_acceptance_never_echo_secret(
    tmp_path: Path, capsys
) -> None:
    episode = _copy_episode(tmp_path)
    source_path = episode / "traces.jsonl"
    secret = "Bearer abcDEF123456789012345678901234"
    source = source_path.read_bytes() + ("{invalid_json " + secret + "\n").encode()
    source_path.write_bytes(source)
    manifest_path = episode / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["sha256"] = hashlib.sha256(source).hexdigest()
    manifest["sources"][0]["bytes"] = len(source)
    manifest_path.write_text(json.dumps(manifest))
    root = tmp_path / "artifacts"
    base = ["normalize", "--episode-dir", str(episode), "--artifact-root", str(root)]
    assert main([*base, "--output-dir", "strict"]) == 2
    assert not (root / "strict/spans.jsonl").exists()
    failed = json.loads((root / "strict/report.json").read_text())
    assert failed["status"] == "failed" and failed["report"]["rejected_count"] == 1
    assert main([*base, "--output-dir", "partial", "--allow-partial"]) == 0
    partial = json.loads((root / "partial/report.json").read_text())
    assert partial["status"] == "partial"
    assert partial["report"]["accepted_count"] == 71
    assert partial["report"]["diagnostics"][0]["code"] == "malformed_json"
    assert secret not in (root / "strict/report.json").read_text()
    assert secret not in (root / "partial/report.json").read_text()
    assert secret not in capsys.readouterr().out


def test_cli_refuses_output_traversal(tmp_path: Path) -> None:
    episode = _copy_episode(tmp_path)
    root = tmp_path / "artifacts"
    assert (
        main(
            [
                "normalize",
                "--episode-dir",
                str(episode),
                "--artifact-root",
                str(root),
                "--output-dir",
                "../escape",
            ]
        )
        == 2
    )
    assert not (tmp_path / "escape").exists()


def test_cli_source_hash_mismatch_has_failed_report(tmp_path: Path) -> None:
    episode = _copy_episode(tmp_path)
    source = episode / "traces.jsonl"
    source.write_bytes(source.read_bytes() + b"\n")
    root = tmp_path / "artifacts"
    assert (
        main(
            [
                "normalize",
                "--episode-dir",
                str(episode),
                "--artifact-root",
                str(root),
                "--output-dir",
                "hash-failure",
                "--allow-partial",
            ]
        )
        == 2
    )
    report = json.loads((root / "hash-failure/report.json").read_text())
    assert report["status"] == "failed"
    assert "source_hash_mismatch" in {
        diagnostic["code"] for diagnostic in report["report"]["diagnostics"]
    }
    assert not (root / "hash-failure/spans.jsonl").exists()
