"""Docker-only bootstrap for the complete local operator application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import httpx

from tracecause.api.resources import Resources, open_resources
from tracecause.config.settings import RuntimeSettings, load_settings
from tracecause.contracts.models import EpisodeManifest, InvestigationRequest, InvestigationResult
from tracecause.ingestion.source_allowlist import resolve_episode_sources
from tracecause.investigation.generation import (
    PINNED_MANIFEST_DIGEST,
    PINNED_MODEL,
    OllamaProvider,
    QwenTokenCounter,
)
from tracecause.investigation.templates import TEMPLATE_VERSION
from tracecause.operational.seed import seed_operational_episodes
from tracecause.operations import prefetch
from tracecause.retrieval.embeddings import EmbeddingError, MiniLMEmbedder
from tracecause.storage.schema import EpisodeRow, SnapshotRow


def models() -> None:
    settings = load_settings(Path("configs/defaults.yaml"))
    try:
        MiniLMEmbedder()
        QwenTokenCounter()
    except (EmbeddingError, OSError, ValueError):
        # Downloads only during this explicit bootstrap job, never in API requests.
        prefetch()
        QwenTokenCounter()
    with httpx.Client(base_url=settings.ollama_url, trust_env=False, timeout=15) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        if not any(row.get("name") == PINNED_MODEL for row in response.json()["models"]):
            print(
                "Downloading the pinned Qwen model into the persistent Ollama volume.", flush=True
            )
            response = client.post(
                "/api/pull", json={"model": PINNED_MODEL, "stream": False}, timeout=3600
            )
            response.raise_for_status()
            if response.json().get("status") != "success":
                raise RuntimeError("Ollama model download did not complete")
    provider = OllamaProvider(
        settings, prompt_root=Path.cwd(), slot_path=settings.artifact_root / "generation.lock"
    )
    try:
        capability = provider.capability()
        if not capability["digest_match"]:
            raise RuntimeError("downloaded Qwen digest does not match the pinned model")
        probe = provider.probe_schema()
        if not probe["schema_supported"]:
            raise RuntimeError("local model schema/readiness probe failed")
    finally:
        provider.client.close()
    print(
        json.dumps(
            {
                "embeddings_ready": True,
                "qwen_digest": PINNED_MANIFEST_DIGEST,
                "schema_probe": "PASS",
            }
        )
    )


def seed_fingerprint() -> str:
    digest = hashlib.sha256(TEMPLATE_VERSION.encode())
    for root in (
        Path("data/demo"),
        Path("data/runbooks/demo"),
        Path("data/evaluation/published"),
        Path("data/operational-lab/episodes"),
    ):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                digest.update(str(path).encode() + b"\0" + path.read_bytes())
    index = Path("data/operational-lab/index.json")
    digest.update(str(index).encode() + b"\0" + index.read_bytes())
    return digest.hexdigest()


def register_bundled_ranker(settings: RuntimeSettings) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "tracecause",
            "ranking",
            "register",
            "--output-root",
            "models/candidate-logistic-v4",
            "--artifact-root",
            str(settings.artifact_root),
            "--db-host",
            settings.pg_host,
            "--db-port",
            str(settings.pg_port),
            "--db-name",
            settings.pg_database,
            "--db-user",
            settings.pg_user,
            "--password-file",
            os.environ["TRACECAUSE_PG_PASSWORD_FILE"],
        ],
        check=True,
        timeout=60,
    )


def _existing_seed_result(
    resources: Resources,
    manifest: EpisodeManifest,
    manifest_hash: str,
    investigation_id: UUID,
) -> InvestigationResult:
    """Reuse an immutable, source-valid demo when other bundled datasets change."""
    result = resources.investigations.show(manifest.workspace_id, investigation_id)
    if (
        result.episode_id != manifest.episode_id
        or result.provenance.input_manifest_hash != manifest_hash
    ):
        raise RuntimeError("seed marker investigation differs from bundled demo source")
    with resources.factory() as session:
        episode = session.get(EpisodeRow, manifest.episode_id)
        if (
            episode is None
            or episode.workspace_id != manifest.workspace_id
            or episode.ingestion_status != "IMPORTED"
            or episode.manifest_hash != manifest_hash
            or episode.active_snapshot_id is None
        ):
            raise RuntimeError("seed marker episode differs from the ready bundled demo")
        # An operator may have selected another valid snapshot since bootstrap.
        # Both snapshots must remain usable; neither pointer is changed here.
        for snapshot_id in {episode.active_snapshot_id, result.snapshot_id}:
            snapshot = session.get(SnapshotRow, snapshot_id)
            if (
                snapshot is None
                or snapshot.status != "READY"
                or snapshot.episode_id != episode.id
                or snapshot.workspace_id != manifest.workspace_id
            ):
                raise RuntimeError("seed marker snapshot is unavailable or outside scope")
    for item in result.evidence:
        resources.investigations.evidence(
            manifest.workspace_id, result.investigation_id, item.evidence_id
        )
    return result


def _write_seed_marker(
    marker: Path,
    fingerprint: str,
    result: InvestigationResult,
    operational: list[dict[str, str]],
) -> None:
    temporary = marker.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(
            {
                "fingerprint": fingerprint,
                "investigation_id": str(result.investigation_id),
                "operational_episodes": operational,
            },
            output,
        )
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(marker)


def seed() -> None:
    settings = load_settings(Path("configs/defaults.yaml"))
    marker = settings.artifact_root / "full-stack-seed.json"
    fingerprint = seed_fingerprint()
    demo_root = Path("data/demo/episode")
    manifest_bytes = (demo_root / "manifest.json").read_bytes()
    manifest = EpisodeManifest.model_validate_json(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    for source, path in zip(
        manifest.sources, resolve_episode_sources(demo_root, manifest), strict=True
    ):
        raw = path.read_bytes()
        if len(raw) != source.bytes or hashlib.sha256(raw).hexdigest() != source.sha256:
            raise ValueError("bundled demo source differs from manifest")
    resources = open_resources(settings)
    try:
        if marker.is_symlink():
            raise ValueError("bootstrap marker must not be a symlink")
        if marker.exists():
            saved = json.loads(marker.read_bytes())
            result = _existing_seed_result(
                resources, manifest, manifest_hash, UUID(saved["investigation_id"])
            )
            register_bundled_ranker(settings)
            operational = seed_operational_episodes(resources, Path("data/operational-lab"))
            _write_seed_marker(marker, fingerprint, result, operational)
            print(
                json.dumps(
                    {
                        "seed_already_ready": True,
                        "bundled_corpus_updated": saved["fingerprint"] != fingerprint,
                        "investigation_id": str(result.investigation_id),
                        "operational_episodes_ready": len(operational),
                    }
                )
            )
            return
        subprocess.run(
            [sys.executable, "-m", "tracecause.operations", "seed"], check=True, timeout=240
        )
        register_bundled_ranker(settings)
        with resources.factory() as session:
            episode = session.get(EpisodeRow, manifest.episode_id)
            assert episode is not None and episode.active_snapshot_id is not None
            snapshot_id = episode.active_snapshot_id
        result = resources.investigations.investigate(
            manifest.workspace_id,
            InvestigationRequest.model_validate(
                {
                    "episode_id": manifest.episode_id,
                    "snapshot_id": snapshot_id,
                    "as_of": manifest.event_interval.end,
                    "temporal_mode": "event_time_replay",
                    "question": "Review the eligible measurements and source evidence.",
                    "retrieval_mode": "GRAPH",
                    "ranking_mode": "heuristic",
                    "generation_mode": "template",
                }
            ),
        )
        operational = seed_operational_episodes(resources, Path("data/operational-lab"))
        _write_seed_marker(marker, fingerprint, result, operational)
        print(json.dumps({"seed_ready": True, "investigation_id": str(result.investigation_id)}))
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("models", "seed"))
    args = parser.parse_args()
    if args.action == "models":
        models()
    else:
        seed()


if __name__ == "__main__":
    main()
