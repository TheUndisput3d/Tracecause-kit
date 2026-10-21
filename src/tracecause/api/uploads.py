"""Bounded multipart staging into a server-selected, flat local directory."""

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import UploadFile

from tracecause.api.errors import ApiError
from tracecause.contracts.models import EpisodeManifest
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID

FILE_LIMIT = 25 * 1024 * 1024
BUNDLE_LIMIT = 100 * 1024 * 1024


async def stage_upload(request: Request, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / str(uuid4())
    directory.mkdir(mode=0o700)
    try:
        async with request.form(max_files=100, max_fields=1, max_part_size=128 * 1024) as form:
            raw_manifest = form.get("manifest")
            if not isinstance(raw_manifest, str) or len(raw_manifest.encode()) > 128 * 1024:
                raise ApiError(422, "invalid_manifest", "A bounded manifest field is required.")
            manifest = EpisodeManifest.model_validate_json(raw_manifest)
            if manifest.workspace_id != DEFAULT_WORKSPACE_ID or manifest.baseline_interval is None:
                raise ApiError(
                    422,
                    "invalid_manifest",
                    "The manifest requires the configured workspace and a baseline.",
                )
            expected = {source.logical_name: source for source in manifest.sources}
            if len(expected) != len(manifest.sources) or "manifest.json" in expected:
                raise ApiError(
                    422,
                    "invalid_sources",
                    "Source names must be distinct and cannot use the manifest name.",
                )
            if sum(source.bytes for source in expected.values()) > BUNDLE_LIMIT:
                raise ApiError(413, "upload_too_large", "The bundle exceeds the upload limit.")
            seen: set[str] = set()
            for field, upload in form.multi_items():
                if field == "manifest" and isinstance(upload, str):
                    continue
                if field != "files" or not isinstance(upload, UploadFile):
                    raise ApiError(
                        422, "invalid_parts", "Only manifest and named file parts are accepted."
                    )
                name = upload.filename or ""
                source = expected.get(name)
                if source is None or name in seen:
                    raise ApiError(
                        422, "invalid_sources", "A source name is undeclared or duplicated."
                    )
                suffix = {
                    "otlp_json": ".json",
                    "otlp_jsonl": ".jsonl",
                    "markdown": ".md",
                    "operational_json": ".json",
                }[source.kind]
                if not name.endswith(suffix) or source.bytes > FILE_LIMIT:
                    raise ApiError(
                        413 if source.bytes > FILE_LIMIT else 422,
                        "invalid_source",
                        "The source extension or size is invalid.",
                    )
                seen.add(name)
                count, digest = 0, hashlib.sha256()
                with (directory / name).open("xb") as output:
                    while chunk := await upload.read(64 * 1024):
                        count += len(chunk)
                        if count > FILE_LIMIT or count > source.bytes:
                            raise ApiError(
                                413,
                                "upload_too_large",
                                "A file exceeds its declared size or upload limit.",
                            )
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if count != source.bytes or digest.hexdigest() != source.sha256:
                    raise ApiError(
                        422, "source_integrity_failed", "A file differs from the manifest."
                    )
            if seen != expected.keys():
                raise ApiError(422, "missing_source", "A declared source is missing.")
            with (directory / "manifest.json").open("xb") as output:
                output.write(raw_manifest.encode())
                output.flush()
                os.fsync(output.fileno())
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return directory
    except BaseException:
        shutil.rmtree(directory)
        raise
