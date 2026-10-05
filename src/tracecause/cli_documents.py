"""Operator CLI for authored-document import."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from tracecause.cli_storage import _connection_args
from tracecause.documents.persistence import ingest_corpus
from tracecause.graph.projection import load_graph_material
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, database_url, make_engine, sessions


def add_document_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    documents = subparsers.add_parser("documents", help="authored document corpus commands")
    actions = documents.add_subparsers(dest="document_action", required=True)
    ingest = actions.add_parser("ingest")
    _connection_args(ingest)
    ingest.add_argument("--corpus-dir", type=Path, required=True)
    ingest.add_argument("--artifact-root", type=Path, required=True)
    review = actions.add_parser("review")
    _connection_args(review)
    review.add_argument("--episode-id", type=UUID, required=True)
    review.add_argument("--document-cutoff-ns", type=int)
    review.add_argument("--artifact-root", type=Path, required=True)


def run_documents(args: argparse.Namespace) -> int:
    try:
        if not 1 <= args.db_port <= 65535:
            raise ValueError("invalid database port")
        url = database_url(
            host=args.db_host,
            port=args.db_port,
            database=args.db_name,
            user=args.db_user,
            password_file=args.password_file,
        )
        engine = make_engine(url)
        try:
            factory = sessions(engine)
            artifacts = ArtifactRepository(args.artifact_root)
            if args.document_action == "ingest":
                result = asdict(ingest_corpus(factory, artifacts, args.corpus_dir))
            elif args.document_action == "review":
                with factory() as session:
                    material = load_graph_material(
                        session,
                        DEFAULT_WORKSPACE_ID,
                        args.episode_id,
                        artifacts=artifacts,
                        document_cutoff_ns=args.document_cutoff_ns,
                    )
                result = {
                    "episode_id": str(args.episode_id),
                    "document_cutoff_ns": material.document_cutoff_ns,
                    "documents": len(material.document_material.documents),
                    "reviews": material.document_material.review,
                }
            else:
                raise ValueError("unsupported document command")
        finally:
            engine.dispose()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except (ValueError, OSError, SQLAlchemyError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
