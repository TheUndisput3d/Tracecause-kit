"""Compare retrieval strategies against one explicit published snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from neo4j.exceptions import Neo4jError
from sqlalchemy.exc import SQLAlchemyError

from tracecause.cli_storage import _connection_args
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.retrieval.embeddings import EmbeddingError, MiniLMEmbedder
from tracecause.retrieval.engine import RetrievalRequest, Strategy, result_jsonable, retrieve
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, database_url, make_engine, sessions


def add_retrieval_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    retrieval = subparsers.add_parser(
        "retrieval", help="compare evidence retrieval without generation"
    )
    actions = retrieval.add_subparsers(dest="retrieval_action", required=True)
    compare = actions.add_parser("compare")
    _connection_args(compare)
    compare.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
    compare.add_argument("--neo4j-auth-file", type=Path, required=True)
    compare.add_argument("--artifact-root", type=Path, required=True)
    compare.add_argument("--embedding-cache-root", type=Path, required=True)
    compare.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)
    compare.add_argument("--episode-id", type=UUID, required=True)
    compare.add_argument("--snapshot-id", type=UUID, required=True)
    compare.add_argument("--as-of-ns", type=int, required=True)
    compare.add_argument("--mode", choices=("event", "available"), default="event")
    compare.add_argument("--question", required=True)
    compare.add_argument("--symptom-service-key")
    compare.add_argument("--top-k", type=int, default=12)


def run_retrieval(args: argparse.Namespace) -> int:
    try:
        if not 1 <= args.db_port <= 65535:
            raise ValueError("invalid database port")
        request = RetrievalRequest(
            workspace_id=args.workspace_id,
            episode_id=args.episode_id,
            snapshot_id=args.snapshot_id,
            as_of_ns=args.as_of_ns,
            mode=args.mode,
            question=args.question,
            symptom_service_key=args.symptom_service_key,
            top_k=args.top_k,
        )
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
            with driver_from_auth_file(args.neo4j_uri, args.neo4j_auth_file) as driver:
                reader = GraphReader(sessions(engine), driver)
                artifacts = ArtifactRepository(args.artifact_root)
                embedder = MiniLMEmbedder()
                strategies: tuple[Strategy, ...] = ("lexical", "dense", "hybrid", "graph")
                results = {
                    strategy: result_jsonable(
                        retrieve(
                            reader,
                            artifacts,
                            request,
                            strategy=strategy,
                            embedder=embedder,
                            embedding_cache_root=args.embedding_cache_root,
                        )
                    )
                    for strategy in strategies
                }
        finally:
            engine.dispose()
        print(json.dumps(results, sort_keys=True, default=str))
        return 0
    except (ValueError, OSError, SQLAlchemyError, Neo4jError, EmbeddingError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
