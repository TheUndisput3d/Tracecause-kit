"""Offline ranker training, development report and trusted registration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy.exc import SQLAlchemyError

from tracecause.cli_storage import _connection_args
from tracecause.contracts.canonical import semantic_sha256
from tracecause.ranking.model import LinearRanker, ModelCompatibilityError
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, database_url, make_engine, sessions
from tracecause.storage.schema import ModelRegistryRow, SourceArtifactRow

MODEL_NAMESPACE = UUID("071c939d-8385-4911-a8b5-41d07488e68c")


def add_ranking_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    ranking = subparsers.add_parser("ranking", help="train and register the local linear ranker")
    actions = ranking.add_subparsers(dest="ranking_action", required=True)
    train = actions.add_parser("train")
    train.add_argument("--public-root", type=Path, required=True)
    train.add_argument("--gold-root", type=Path, required=True)
    train.add_argument("--output-root", type=Path, required=True)
    report = actions.add_parser("report")
    report.add_argument("--output-root", type=Path, required=True)
    register = actions.add_parser("register")
    _connection_args(register, admin=True)
    register.add_argument("--output-root", type=Path, required=True)
    register.add_argument("--artifact-root", type=Path, required=True)
    register.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)


def run_ranking(args: argparse.Namespace) -> int:
    try:
        root = args.output_root
        if root.is_symlink():
            raise ValueError("unsafe ranker output root")
        if args.ranking_action == "train":
            from tracecause.ranking.train import train_and_report

            result = train_and_report(args.public_root, args.gold_root, root)
            print(
                json.dumps(
                    {
                        "artifact_sha256": result["artifact_sha256"],
                        "selected_default": result["validation"]["selected_default"],
                        "train_identifiable_episodes": result["train_identifiable_episodes"],
                        "development_learned": result["validation"]["development_learned"],
                        "development_heuristic": result["validation"]["development_heuristic"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.ranking_action == "report":
            result = json.loads((root / "development_report.json").read_bytes())
            print(json.dumps(result["validation"], sort_keys=True))
            return 0
        if args.ranking_action != "register":
            raise ValueError("unknown ranking action")
        model_file = root / "model.json"
        if model_file.is_symlink() or not model_file.is_file():
            raise ValueError("ranker model file missing or unsafe")
        raw = model_file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        model = LinearRanker.from_bytes(raw, expected_hash=digest)
        if model.artifact["validation"]["selected_default"] != "learned":
            raise ValueError("development report did not select learned ranker")
        artifacts = ArtifactRepository(args.artifact_root.resolve())
        descriptor = artifacts.put_bytes(raw)
        if descriptor.content_hash != digest:
            raise ValueError("ranker artifact digest mismatch")
        engine = make_engine(
            database_url(
                host=args.db_host,
                port=args.db_port,
                database=args.db_name,
                user=args.db_user,
                password_file=args.password_file,
            )
        )
        try:
            factory = sessions(engine)
            identifier = uuid5(MODEL_NAMESPACE, digest)
            with factory.begin() as session:
                artifact = session.get(SourceArtifactRow, digest)
                if artifact is None:
                    session.add(
                        SourceArtifactRow(
                            content_hash=digest,
                            workspace_id=args.workspace_id,
                            kind="ranker_model_json",
                            byte_size=descriptor.byte_size,
                            relative_storage_key=descriptor.relative_key,
                            schema_version="1.0",
                            redaction_version="derived-from-redacted-v1",
                        )
                    )
                elif (
                    artifact.workspace_id != args.workspace_id
                    or artifact.kind != "ranker_model_json"
                ):
                    raise ValueError("ranker artifact scope conflicts")
                registered = session.get(ModelRegistryRow, identifier)
                if registered is None:
                    session.add(
                        ModelRegistryRow(
                            id=identifier,
                            model_name=model.artifact["model_version"],
                            feature_schema_version=model.artifact["feature_schema_version"],
                            artifact_hash=digest,
                            config_hash=semantic_sha256(model.artifact["training"]),
                            metric_report_hash=semantic_sha256(model.artifact["validation"]),
                            promoted_at=datetime.now(UTC),
                        )
                    )
                elif registered.artifact_hash != digest:
                    raise ValueError("ranker registry identity conflicts")
            print(
                json.dumps(
                    {"model_id": str(identifier), "artifact_sha256": digest, "promoted": True}
                )
            )
            return 0
        finally:
            engine.dispose()
    except (OSError, ValueError, KeyError, SQLAlchemyError, ModelCompatibilityError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
