"""Measure published Neo4j edges against frozen synthetic generator topology.

This offline check reads explicit historical snapshot IDs, not the active pointer.
Generator topology is an evaluation reference, never an input to graph reads.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from typing import Any

from tracecause.evaluation.metrics import rate, score_edges
from tracecause.graph.queries import GraphReader
from tracecause.graph.schema import driver_from_auth_file
from tracecause.retrieval.engine import EvidenceCandidate
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.postgres import database_url, make_engine, sessions

from .benchmark import code_identity
from .graph_reconstruction import evaluate
from .prepare import ROOT, digest, write_json
from .resolve_review import resolve_candidate
from .systems import load_episode


def verify_parent_support(
    observation: dict[str, Any],
    cache: dict[str, dict[tuple[str, str], dict[str, Any]]],
) -> int:
    """Independently follow every measured SERVER's cited parent chain.

    Source identity/scope/time must first be checked with resolve_candidate.
    Merely citing an unrelated ancestor from the caller does not suffice.
    """
    if observation["type"] != "dependency":
        raise ValueError("call support is not a dependency observation")
    refs = observation["supports"]
    indexed = {
        (ref["trace_id"], ref["span_id"]): cache[ref["artifact_hash"]][
            (ref["trace_id"], ref["span_id"])
        ]
        for ref in refs
    }
    ancestors = {(ref["trace_id"], ref["span_id"]) for ref in refs if ref["role"] == "ancestor"}
    measured = [ref for ref in refs if ref["role"] == "measurement"]
    if not measured or len(measured) != observation["sample_count"]:
        raise ValueError("call measurement count differs")
    for ref in measured:
        child = indexed[(ref["trace_id"], ref["span_id"])]
        if child["kind"] != "SERVER" or child["service_key"] != observation["callee_service_key"]:
            raise ValueError("call measurement has wrong kind or callee")
        cursor = child
        visited = {child["span_id"]}
        for _ in range(4):
            parent_id = cursor.get("parent_span_id")
            key = (child["trace_id"], parent_id)
            if parent_id in visited or key not in ancestors:
                raise ValueError("call measurement lacks a cited parent chain")
            visited.add(parent_id)
            parent = indexed[key]
            if (
                parent["kind"] in {"PRODUCER", "CONSUMER"}
                or int(parent["start_ns"]) > int(cursor["end_ns"])
                or int(parent["end_ns"]) < int(cursor["start_ns"])
            ):
                raise ValueError("call parent is asynchronous or outside child time")
            if parent["service_key"] != child["service_key"]:
                if parent["service_key"] != observation["caller_service_key"]:
                    raise ValueError("call parent belongs to a different caller")
                break
            cursor = parent
        else:
            raise ValueError("call parent chain exceeds supported hop limit")
    return len(measured)


def main() -> None:
    root = ROOT / "reports/phase13-graph-projection-v1"
    prepared = ROOT / "reports/phase13-prepared-v2"
    # Reopen/hash-check public sources and frozen private topology. This separate
    # raw-span diagnostic never provides predicted edges to the database reader.
    evaluate(
        ROOT / "data/generated/seed-20261010",
        ROOT / "data/gold/seed-20261010",
        root / "direct-span-reference",
        split="test",
    )
    reference_path = root / "direct-span-reference/synthetic_test_graph_reconstruction.json"
    reference = json.loads(reference_path.read_bytes())
    index = json.loads((prepared / "index.json").read_bytes())
    exports = {item["episode_id"]: item["sha256"] for item in index["episodes"]}
    engine = make_engine(
        database_url(
            host="127.0.0.1",
            port=15432,
            database="tracecause_evaluation_v1",
            user="postgres",
            password_file=ROOT / ".secrets/postgres_admin_password",
        )
    )
    artifacts = ArtifactRepository(ROOT / "reports/phase13-artifacts")
    rows = []
    try:
        with driver_from_auth_file(
            "bolt://127.0.0.1:17687", ROOT / ".secrets/neo4j_auth"
        ) as driver:
            reader = GraphReader(sessions(engine), driver)
            for truth in reference["episodes"]:
                episode_id = truth["episode_id"]
                episode = load_episode(prepared / f"{episode_id}.json", exports[episode_id])
                scope = dict(
                    workspace_id=episode.workspace_id,
                    episode_id=episode.episode_id,
                    snapshot_id=episode.snapshot_id,
                    as_of_ns=episode.cutoff,
                )
                calls = reader.eligible_calls(**scope)
                if calls != episode.calls:
                    raise ValueError("published graph changed from frozen prepared export")
                names = {key: name for name, key in episode.names.items()}
                predicted = [
                    (names[call.caller_service_key], names[call.callee_service_key])
                    for call in calls
                ]
                count, pairs = 0, 0
                cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
                for call in calls:
                    for observation_id in call.support_observation_ids:
                        obs = reader.observation(**scope, observation_id=observation_id)
                        if (obs["caller_service_key"], obs["callee_service_key"]) != (
                            call.caller_service_key,
                            call.callee_service_key,
                        ):
                            raise ValueError("graph edge and support endpoints differ")
                        item = EvidenceCandidate(
                            **scope,
                            evidence_id=f"observation:{observation_id}",
                            kind="observation",
                            text="",
                            service_keys=(),
                            source_locator=obs,
                        )
                        resolve_candidate(item, artifacts, cache)
                        pairs += verify_parent_support(obs, cache)
                        count += 1
                expected = [tuple(edge) for edge in truth["observable_known_edges"]]
                score = asdict(score_edges(predicted, expected))
                rows.append(
                    {
                        "episode_id": episode_id,
                        "snapshot_id": str(episode.snapshot_id),
                        "as_of_ns": episode.cutoff,
                        "prepared_sha256": exports[episode_id],
                        "topology": truth["topology"],
                        "source_sha256": truth["source_sha256"],
                        "predicted_edges": predicted,
                        "known_topology_edges": expected,
                        "score": score,
                        "resolved_observations": count,
                        "verified_parent_pairs": pairs,
                    }
                )
                print(json.dumps({"episodes": len(rows), "total": 25}), flush=True)
    finally:
        engine.dispose()
    hits = sum(row["score"]["true_positive_edges"] for row in rows)
    summary = {
        "status": "completed",
        "origin": "synthetic",
        "split": "test",
        "scope": "published_neo4j_snapshots_with_postgres_and_source_parent_checks",
        "reference": "frozen_generator_topology_restricted_to_present_service_endpoints",
        "limitation": "Known topology coverage is not causal or production accuracy.",
        "episodes": len(rows),
        "micro_precision": asdict(rate(hits, sum(r["score"]["predicted_edges"] for r in rows))),
        "micro_recall": asdict(rate(hits, sum(r["score"]["observable_known_edges"] for r in rows))),
        "resolved_observations": sum(r["resolved_observations"] for r in rows),
        "verified_parent_pairs": sum(r["verified_parent_pairs"] for r in rows),
        "reference_sha256": digest(reference_path),
        "prepared_index_sha256": digest(prepared / "index.json"),
        "code_sha256": code_identity(),
    }
    write_json(root / "results.json", {"summary": summary, "episodes": rows})
    with (root / "episodes.csv").open("w", newline="") as stream:
        flattened = [
            {
                k: v
                for k, v in row.items()
                if k not in {"predicted_edges", "known_topology_edges", "score"}
            }
            | row["score"]
            for row in rows
        ]
        writer = csv.DictWriter(stream, fieldnames=list(flattened[0]))
        writer.writeheader()
        writer.writerows(flattened)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
