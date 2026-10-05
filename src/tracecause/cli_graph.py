"""Operator commands for graph schema, publication and bounded reads."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from neo4j.exceptions import Neo4jError
from sqlalchemy.exc import SQLAlchemyError

from tracecause.cli_storage import _connection_args
from tracecause.graph.maintenance import reconciliation_inventory, retire_snapshot
from tracecause.graph.projection import build_snapshot
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import audit_graph_schema, driver_from_auth_file, migrate_graph
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import DEFAULT_WORKSPACE_ID, database_url, make_engine, sessions


def add_graph_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    graph = subparsers.add_parser("graph", help="graph migration, snapshots and bounded reads")
    actions = graph.add_subparsers(dest="graph_action", required=True)
    for name in (
        "migrate",
        "schema-audit",
        "build",
        "audit",
        "neighbors",
        "paths",
        "observation",
        "subgraph",
        "chunks",
        "retire",
    ):
        cmd = actions.add_parser(name)
        cmd.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
        cmd.add_argument("--neo4j-auth-file", type=Path, required=True)
        if name in {"migrate", "schema-audit"}:
            continue
        _connection_args(cmd)
        if name == "audit":
            continue
        cmd.add_argument("--workspace-id", type=UUID, default=DEFAULT_WORKSPACE_ID)
        cmd.add_argument("--episode-id", type=UUID, required=True)
        if name == "build":
            cmd.add_argument("--artifact-root", type=Path)
            cmd.add_argument("--document-cutoff-ns", type=int)
            continue
        cmd.add_argument("--snapshot-id", type=UUID, required=True)
        if name == "retire":
            cmd.add_argument("--apply", action="store_true")
            continue
        cmd.add_argument("--as-of-ns", type=int, required=True)
        cmd.add_argument("--mode", choices=("event", "available"), default="event")
        if name == "chunks":
            cmd.add_argument("--artifact-root", type=Path, required=True)
        if name == "neighbors":
            cmd.add_argument("--service-key", required=True)
        elif name == "paths":
            cmd.add_argument("--source-service-key", required=True)
            cmd.add_argument("--target-service-key", required=True)
            cmd.add_argument("--max-hops", type=int, default=3)
        elif name == "observation":
            cmd.add_argument("--observation-id", type=UUID, required=True)


def run_graph(args: argparse.Namespace) -> int:
    try:
        result: Any
        with driver_from_auth_file(args.neo4j_uri, args.neo4j_auth_file) as driver:
            if args.graph_action == "migrate":
                result = migrate_graph(driver)
            elif args.graph_action == "schema-audit":
                result = audit_graph_schema(driver)
            else:
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
                    if args.graph_action == "build":
                        result = asdict(
                            build_snapshot(
                                factory,
                                driver,
                                workspace_id=args.workspace_id,
                                episode_id=args.episode_id,
                                artifacts=(
                                    ArtifactRepository(args.artifact_root)
                                    if args.artifact_root is not None
                                    else None
                                ),
                                document_cutoff_ns=args.document_cutoff_ns,
                            )
                        )
                    elif args.graph_action == "audit":
                        result = reconciliation_inventory(factory, driver)
                    elif args.graph_action == "retire":
                        result = asdict(
                            retire_snapshot(
                                factory,
                                driver,
                                workspace_id=args.workspace_id,
                                episode_id=args.episode_id,
                                snapshot_id=args.snapshot_id,
                                apply=args.apply,
                            )
                        )
                    else:
                        reader = GraphReader(factory, driver)
                        scope = dict(
                            workspace_id=args.workspace_id,
                            episode_id=args.episode_id,
                            snapshot_id=args.snapshot_id,
                            as_of_ns=args.as_of_ns,
                            mode=args.mode,
                        )
                        if args.graph_action == "neighbors":
                            result = [
                                asdict(item)
                                for item in reader.neighbors(
                                    **scope,
                                    service_key=args.service_key,
                                )
                            ]
                        elif args.graph_action == "paths":
                            result = reader.support_paths(
                                **scope,
                                source_service_key=args.source_service_key,
                                target_service_key=args.target_service_key,
                                max_hops=args.max_hops,
                            )
                        elif args.graph_action == "observation":
                            result = reader.observation(
                                **scope,
                                observation_id=args.observation_id,
                            )
                        elif args.graph_action == "subgraph":
                            result = reader.subgraph(**scope)
                        elif args.graph_action == "chunks":
                            result = reader.eligible_document_chunks(
                                **scope, artifacts=ArtifactRepository(args.artifact_root)
                            )
                        else:
                            raise ValueError("unsupported graph command")
                finally:
                    engine.dispose()
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except (ValueError, OSError, SQLAlchemyError, Neo4jError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
