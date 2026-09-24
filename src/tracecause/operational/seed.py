"""Idempotent Docker startup imports using the normal durable API pipeline."""

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from tracecause.api.resources import Resources
from tracecause.contracts.models import EpisodeManifest
from tracecause.graph.maintenance import reader_lease
from tracecause.graph.projection import build_snapshot, load_graph_material, validate_projection
from tracecause.ingestion.source_allowlist import resolve_episode_sources
from tracecause.jobs.api_jobs import ApiJobs
from tracecause.jobs.api_worker import ApiWorker
from tracecause.operational.models import OperationalBundle
from tracecause.operational.replay import REPLAY_VERSION, materialize_replay
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID
from tracecause.storage.schema import EpisodeRow, JobRow, SnapshotRow, SpanRow

DOCUMENT_SCOPE_VERSION = "explicit-service-scope-v1"


@dataclass(frozen=True)
class SeedBundle:
    directory: Path
    manifest: EpisodeManifest
    manifest_hash: str
    default_as_of: datetime
    default_symptom_service_id: str


def bundled_episodes(root: Path) -> tuple[SeedBundle, ...]:
    """The public capture index supplies a useful cursor, never an expected answer."""
    raw = json.loads((root / "index.json").read_bytes())
    entries = raw["episodes"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 30:
        raise ValueError("bundled operational episode count is invalid")
    result = []
    seen: set[UUID] = set()
    for entry in entries:
        identifier = UUID(entry["episode_id"])
        directory = root / "episodes" / str(identifier)
        manifest_bytes = (directory / "manifest.json").read_bytes()
        manifest = EpisodeManifest.model_validate_json(manifest_bytes)
        if manifest.episode_id != identifier or manifest.workspace_id != DEFAULT_WORKSPACE_ID:
            raise ValueError("bundled operational episode scope differs from index")
        if identifier in seen or not any(
            source.kind == "operational_json" for source in manifest.sources
        ):
            raise ValueError("bundled operational membership is invalid")
        seen.add(identifier)
        sources = resolve_episode_sources(directory, manifest)
        operation_bundles = []
        for metadata, path in zip(manifest.sources, sources, strict=True):
            source = path.read_bytes()
            if (
                len(source) != metadata.bytes
                or hashlib.sha256(source).hexdigest() != metadata.sha256
            ):
                raise ValueError("bundled operational source differs from manifest")
            if metadata.kind == "operational_json":
                operation_bundles.append(OperationalBundle.model_validate_json(source))
        if len(operation_bundles) != 1:
            raise ValueError("bundled operational source membership must contain one receipt")
        symptom_keys = {
            event.service.key(manifest.workspace_id)
            for event in operation_bundles[0].events
            if event.service.name == entry["symptom_service"]
        }
        if len(symptom_keys) != 1:
            raise ValueError("public symptom service is absent or ambiguous in source receipts")
        cursor = datetime.fromisoformat(entry["analysis_end"])
        if (
            cursor.tzinfo is None
            or not manifest.event_interval.start <= cursor <= manifest.event_interval.end
        ):
            raise ValueError("bundled demonstration cursor is outside the episode")
        result.append(
            SeedBundle(
                directory,
                manifest,
                hashlib.sha256(manifest_bytes).hexdigest(),
                cursor,
                next(iter(symptom_keys)),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (item.manifest.event_interval.start, str(item.manifest.episode_id)),
        )
    )


def _stage(resources: Resources, bundle: SeedBundle) -> Path:
    resources.staging.mkdir(parents=True, exist_ok=True)
    destination = resources.staging / str(
        uuid5(NAMESPACE_URL, "tracecause-seed:" + bundle.manifest_hash)
    )
    if destination.exists():
        paths = resolve_episode_sources(destination, bundle.manifest)
        if (
            hashlib.sha256((destination / "manifest.json").read_bytes()).hexdigest()
            != bundle.manifest_hash
        ):
            raise ValueError("existing seed staging manifest differs")
        if any(
            hashlib.sha256(path.read_bytes()).hexdigest() != metadata.sha256
            for metadata, path in zip(bundle.manifest.sources, paths, strict=True)
        ):
            raise ValueError("existing seed staging source differs")
        return destination
    temporary = resources.staging / str(uuid4())
    temporary.mkdir(mode=0o700)
    try:
        for source in ("manifest.json", *(item.logical_name for item in bundle.manifest.sources)):
            shutil.copyfile(bundle.directory / source, temporary / source)
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return destination


def _verify_ready(
    resources: Resources, bundle: SeedBundle, *, allow_upgrade: bool = True
) -> UUID | None:
    with resources.factory() as session:
        episode = session.get(EpisodeRow, bundle.manifest.episode_id)
        if (
            episode is None
            or episode.ingestion_status != "IMPORTED"
            or not episode.active_snapshot_id
        ):
            return None
        if (
            episode.workspace_id != bundle.manifest.workspace_id
            or episode.manifest_hash != bundle.manifest_hash
        ):
            raise ValueError("existing operational seed episode differs from bundled manifest")
        snapshot = session.get(SnapshotRow, episode.active_snapshot_id)
        if (
            snapshot is None
            or snapshot.status != "READY"
            or snapshot.episode_id != episode.id
            or snapshot.workspace_id != episode.workspace_id
        ):
            raise RuntimeError("operational seed active snapshot is unavailable")
        if not (snapshot.membership or {}).get("operational_hashes"):
            raise RuntimeError("operational seed snapshot has no source membership")
        snapshot_id = snapshot.id
        membership = snapshot.membership
        content_hash = snapshot.content_hash
    with reader_lease(
        resources.factory,
        bundle.manifest.workspace_id,
        bundle.manifest.episode_id,
        snapshot_id,
        renew=True,
    ):
        with resources.factory() as session:
            material = load_graph_material(
                session,
                bundle.manifest.workspace_id,
                bundle.manifest.episode_id,
                artifacts=resources.artifacts,
                membership=membership,
            )
        if material.content_hash != content_hash:
            raise RuntimeError("operational seed snapshot source membership hash differs")
        validate_projection(resources.driver, snapshot_id, material, complete=True)
    if (
        (membership or {}).get("operational_replay_version") != REPLAY_VERSION
        or (membership or {}).get("document_scope_version") != DOCUMENT_SCOPE_VERSION
        or not (membership or {}).get("operational_replay_modes")
    ):
        if not allow_upgrade:
            raise RuntimeError("operational snapshot upgrade did not publish current membership")
        materialize_replay(
            resources.factory,
            resources.artifacts,
            bundle.manifest.workspace_id,
            bundle.manifest.episode_id,
        )
        build_snapshot(
            resources.factory,
            resources.driver,
            workspace_id=bundle.manifest.workspace_id,
            episode_id=bundle.manifest.episode_id,
            artifacts=resources.artifacts,
        )
        return _verify_ready(resources, bundle, allow_upgrade=False)
    return snapshot_id


def seed_operational_episodes(resources: Resources, root: Path) -> list[dict[str, str]]:
    jobs = ApiJobs(resources.factory, resources.settings)
    worker = ApiWorker(resources, worker_id="operational-bootstrap-" + str(uuid4()))
    results = []
    for bundle in bundled_episodes(root):
        snapshot_id = _verify_ready(resources, bundle)
        if snapshot_id is None:
            directory = _stage(resources, bundle)
            submitted = jobs.submit_import(
                directory, "operational-bootstrap:" + bundle.manifest_hash
            )
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                with resources.factory() as session:
                    job = session.get(JobRow, submitted.id)
                    if job is None:
                        raise RuntimeError("operational seed import job disappeared")
                    status, safe_error = job.status, job.safe_error
                if status == "SUCCEEDED":
                    break
                if status in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(f"operational seed import {status}: {safe_error}")
                if worker.run_once(job_id=submitted.id) is None:
                    time.sleep(0.25)
            else:
                raise RuntimeError("operational seed import exceeded startup deadline")
            snapshot_id = _verify_ready(resources, bundle)
            if snapshot_id is None:
                raise RuntimeError("operational seed import did not publish a ready snapshot")
        with resources.factory.begin() as session:
            episode = session.get(EpisodeRow, bundle.manifest.episode_id)
            if episode is None:
                raise RuntimeError("operational seed episode disappeared")
            actual_key = session.scalar(
                select(SpanRow.service_key)
                .where(
                    SpanRow.workspace_id == bundle.manifest.workspace_id,
                    SpanRow.episode_id == bundle.manifest.episode_id,
                    SpanRow.service_key == bundle.default_symptom_service_id,
                )
                .limit(1)
            )
            if actual_key != bundle.default_symptom_service_id:
                raise ValueError("public symptom service lacks matching telemetry identity")
            episode.data_quality = {
                **episode.data_quality,
                "default_as_of": bundle.default_as_of.isoformat(),
                "default_symptom_service_id": bundle.default_symptom_service_id,
            }
        results.append(
            {
                "episode_id": str(bundle.manifest.episode_id),
                "snapshot_id": str(snapshot_id),
                "default_as_of": bundle.default_as_of.isoformat(),
                "default_symptom_service_id": bundle.default_symptom_service_id,
            }
        )
        print(json.dumps({"operational_episode_ready": results[-1]}), flush=True)
    return results
