"""Controlled KG competency evaluation, not diagnosis or production accuracy.

The expected reachable service sets below are declared independently of the runtime
traversal. Both methods receive the same source-verified authored checks and graph.
The baseline matches only the selected service; the graph method joins directed calls.
"""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tracecause.contracts.models import GraphResponse
from tracecause.documents.assertions import DeterministicAssertionExtractor
from tracecause.documents.linking import ServiceIdentity, resolve_mentions
from tracecause.documents.markdown import chunk_document, model_tokenizer, parse_document
from tracecause.documents.verification import verified_assertions
from tracecause.graph.knowledge import reachable_checks

NAMES = ("storefront", "checkout", "payment", "inventory", "catalog", "cache")
CASES = (
    ("isolated", (), "a"),
    ("one-hop", (("a", "b"),), "ab"),
    ("reverse-only", (("b", "a"),), "a"),
    ("chain-hop-cap", (("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")), "abcd"),
    ("star", (("a", "b"), ("a", "c"), ("a", "d")), "abcd"),
    ("cycle", (("a", "b"), ("b", "a"), ("b", "c")), "abc"),
    ("disconnected", (("a", "b"), ("c", "d")), "ab"),
    ("diamond", (("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")), "abcd"),
)


def evaluate():
    keys = dict(zip("abcdef", NAMES, strict=True))
    services = tuple(ServiceIdentity(n, "demo", "test", n) for n in NAMES)
    material = []
    for index, name in enumerate(NAMES):
        raw = (
            f"---\nsource_id: competency-{index}\nversion: '1.0'\ntitle: {name} checks\n"
            "origin: authored_demo\ndocument_type: runbook\n"
            "published_at: 2025-12-01T00:00:00Z\nlicense: Apache-2.0\n"
            "attribution: TraceCause synthetic competency fixture\n---\n"
            f"# Diagnostic check\n\n**Check:** Inspect {name} request duration.\n"
        ).encode()
        parsed = parse_document(raw)
        links = resolve_mentions(parsed, services)
        parts = chunk_document(parsed, model_tokenizer())
        chunk_rows = [
            dict(chunk_id=str(c.id), start_char=c.start_char, end_char=c.end_char) for c in parts
        ]
        projected = []
        for item in DeterministicAssertionExtractor().extract(parsed, links):
            projected.append(
                dict(
                    assertion_id=str(item.id),
                    document_id=str(item.document_id),
                    predicate=item.predicate,
                    quote=item.quote,
                    start_char=item.start_char,
                    end_char=item.end_char,
                    extractor_version=item.extractor_version,
                    verification_status=item.verification_status,
                    service_keys=[item.subject_service_key],
                    chunk_ids=[
                        str(c.id)
                        for c in parts
                        if c.start_char < item.end_char and c.end_char > item.start_char
                    ],
                )
            )
        assertions = verified_assertions(parsed, links, projected, chunk_rows)
        for part in parts:
            material.append(
                dict(
                    source_id=parsed.header.source_id,
                    title=parsed.header.title,
                    chunk_id=str(part.id),
                    start_char=part.start_char,
                    verified_assertions=[
                        a
                        for a in assertions
                        if a["start_char"] >= part.start_char and a["end_char"] <= part.end_char
                    ],
                )
            )
    cases = []
    for name, pairs, expected in CASES:
        graph = GraphResponse(
            episode_id=uuid5(NAMESPACE_URL, "competency:" + name),
            snapshot_id=uuid5(NAMESPACE_URL, "competency:snapshot:" + name),
            as_of="2026-01-01T00:00:00Z",
            temporal_mode="event_time_replay",
            nodes=[
                dict(service_id=n, service_key=n, display_name=n, quality_flags=[]) for n in NAMES
            ],
            edges=[
                dict(
                    edge_id=a + b,
                    source_service_id=keys[a],
                    target_service_id=keys[b],
                    relationship="CALLS",
                    supporting_evidence_ids=["synthetic-call:" + a + b],
                )
                for a, b in pairs
            ],
            truncated=False,
            warnings=[],
        )
        result = reachable_checks(graph, tuple(material), "storefront")
        target = {keys[k] for k in expected}
        actual = {c.service_id for c in result.checks}
        baseline = {
            a["service_key"]
            for c in material
            for a in c["verified_assertions"]
            if a["predicate"] == "SUGGESTS_CHECK" and a["service_key"] == "storefront"
        }
        cases.append(
            dict(
                name=name,
                expected=sorted(target),
                graph=sorted(actual),
                direct_service_lookup=sorted(baseline),
                exact_match=actual == target,
                graph_tp=len(actual & target),
                graph_fp=len(actual - target),
                baseline_tp=len(baseline & target),
                targets=len(target),
                proof_paths=[c.model_dump(mode="json") for c in result.checks],
            )
        )
    targets = sum(c["targets"] for c in cases)
    return dict(
        protocol="directed-runbook-competency-v1",
        origin="synthetic_controlled",
        scope="Eight declared topologies; pure reasoning and real document extraction. "
        "Synthetic call IDs are not database evidence. Database/API citation and "
        "tamper gates are separate integration tests. No diagnosis accuracy claim.",
        cases=cases,
        totals=dict(
            cases=len(cases),
            expected_checks=targets,
            graph_recalled=sum(c["graph_tp"] for c in cases),
            graph_false_positives=sum(c["graph_fp"] for c in cases),
            direct_lookup_recalled=sum(c["baseline_tp"] for c in cases),
            exact_match_cases=sum(c["exact_match"] for c in cases),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be new; retain prior measurements")
    result = evaluate()
    result["source_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__),
            Path("src/tracecause/graph/knowledge.py"),
            Path("src/tracecause/documents/verification.py"),
        )
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"]))
    if result["totals"]["exact_match_cases"] != len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
