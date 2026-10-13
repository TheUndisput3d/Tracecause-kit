"""Audit captured operational reasoning through the real read-only API.

Evaluation controls stay in this offline process. The server receives only public
episode/snapshot identifiers, a replay cutoff and a symptom service. Failed cases
are retained; this script never modifies a capture, model or expected outcome.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from tracecause.contracts.models import GraphResponse
from tracecause.operational.models import OperationalBundle
from tracecause.operational.reasoning_models import ChangeReasoningResponse

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("graph_temporal", "chronological", "direct_service", "without_graph", "without_time")
TOP_K = 3


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return calendar.timegm(value.utctimetuple()) * 1_000_000_000 + value.microsecond * 1000


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def methods(result: ChangeReasoningResponse) -> dict[str, list[str]]:
    """Same complete source corpus and output budget; removed features are ignored."""
    if result.omitted_changes:
        raise ValueError("comparison requires the complete candidate pool, not a truncated list")
    chronological = sorted(result.changes, key=lambda c: (-ns(c.occurred_at), str(c.event_id)))
    prior_anomalous = [
        change
        for change in chronological
        if change.signal is not None
        and change.signal.degradation_detected
        and change.signal.first_degradation_bucket_ns is not None
        and ns(change.occurred_at) < int(change.signal.first_degradation_bucket_ns)
    ]
    candidates = {
        "graph_temporal": [c for c in result.changes if c.priority == "review_first"],
        "chronological": chronological,
        "direct_service": [c for c in chronological if c.service_id == result.symptom_service_id],
        "without_graph": prior_anomalous,
        "without_time": sorted(
            [
                c
                for c in chronological
                if c.service_ids and c.signal is not None and c.signal.degradation_detected
            ],
            key=lambda c: (
                c.downstream_degradation_count > 0,
                -ns(c.occurred_at),
                len(c.call_edges),
                str(c.event_id),
            ),
        ),
    }
    return {
        name: [str(change.event_id) for change in rows[:TOP_K]] for name, rows in candidates.items()
    }


def get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(path, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("API response must be an object")
    return data


def inspect_citations(
    client: httpx.Client,
    payload: dict[str, Any],
    changes: ChangeReasoningResponse,
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    requests: set[tuple[str, str, str, str]] = set()

    def add(evidence_id: str, at_scope: dict[str, Any]) -> None:
        requests.add(
            (
                str(at_scope["episode_id"]),
                str(at_scope["snapshot_id"]),
                str(at_scope["as_of"]),
                evidence_id,
            )
        )

    for change in changes.changes:
        add(change.evidence_id, scope)
        if change.signal:
            add(change.signal.observation_evidence_id, scope)
            for evidence in change.signal.baseline_evidence_ids:
                add(evidence, scope)
        for edge in change.call_edges:
            for evidence in edge.supporting_evidence_ids:
                add(evidence, scope)
        for downstream in change.downstream_degradations:
            add(downstream.signal.observation_evidence_id, scope)
            for evidence in downstream.signal.baseline_evidence_ids:
                add(evidence, scope)
            for edge in downstream.edges:
                for evidence in edge.supporting_evidence_ids:
                    add(evidence, scope)
    for match in payload["history"]["matches"]:
        previous_scope = dict(
            episode_id=match["episode_id"], snapshot_id=match["snapshot_id"], as_of=match["as_of"]
        )
        for difference in match["state_differences"]:
            for field in ("previous_evidence", "current_evidence"):
                if difference[field]:
                    add(difference[field]["evidence_id"], difference[field])
        for field in ("matching_signal_evidence", "current_signal_evidence"):
            for evidence in match[field]:
                add(evidence["evidence_id"], evidence)
        for edge in match["new_dependencies"]:
            for evidence in edge["supporting_evidence_ids"]:
                add(evidence, scope)
        for edge in match["previous_only_dependencies"]:
            for evidence in edge["supporting_evidence_ids"]:
                add(evidence, previous_scope)
    results = []
    for episode, snapshot, cutoff, identifier in sorted(requests):
        params = dict(
            episode_id=episode,
            snapshot_id=snapshot,
            as_of=cutoff,
            temporal_mode="event_time_replay",
        )
        path = "/api/v1/evidence/"
        if identifier.startswith("operation:"):
            path = "/api/v1/operations/evidence/"
        elif identifier.startswith("handoff:"):
            path = "/api/v1/operations/handoffs/"
        evidence = get(client, path + quote(identifier, safe=""), params)
        if evidence.get("evidence_id") != identifier:
            raise ValueError("resolved citation has a different identity")
        if evidence.get("episode_id") != episode or evidence.get("snapshot_id") != snapshot:
            raise ValueError("resolved citation has a different scope")
        if identifier.startswith("operation:") and dt(evidence["event"]["occurred_at"]) > dt(
            cutoff
        ):
            raise ValueError("operation citation contains a future event")
        if identifier.startswith("handoff:") and any(
            int(evidence[role]["end_ns"]) > ns(dt(cutoff)) for role in ("producer", "consumer")
        ):
            raise ValueError("handoff citation contains a future span")
        results.append(
            dict(
                episode_id=episode,
                snapshot_id=snapshot,
                as_of=cutoff,
                evidence_id=identifier,
                response_sha256=hashlib.sha256(
                    json.dumps(evidence, sort_keys=True).encode()
                ).hexdigest(),
            )
        )
    return results


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise ValueError("choose a new output directory; prior results must remain intact")
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("this controlled audit requires an explicit local API")
    index = json.loads(args.index.read_text())
    protocol = json.loads(args.protocol.read_text())
    if protocol.get("no_tuning_on_result") is not True:
        raise ValueError("an explicitly declared evaluation protocol is required")
    source_hashes = {
        str(path.relative_to(ROOT)): digest(path)
        for path in [
            Path(__file__),
            ROOT / "src/tracecause/operational/reasoning.py",
            ROOT / "src/tracecause/operational/reasoning_models.py",
            ROOT / "src/tracecause/operational/handoffs.py",
            ROOT / "src/tracecause/telemetry/signals.py",
        ]
    }
    queries = []
    for row in index["episodes"]:
        episode_id = row["episode_id"]
        truth_path = args.truth_root / episode_id / "truth.json"
        truth = json.loads(truth_path.read_text())
        source_dir = args.index.parent / "episodes" / episode_id
        operations_path = source_dir / "operational.json"
        bundle = OperationalBundle.model_validate_json(operations_path.read_bytes())
        if (
            digest(operations_path) != row["operational_sha256"]
            or row["source_sha256"] != truth["source_sha256"]
            or digest(source_dir / "traces.jsonl") != row["source_sha256"]
        ):
            raise ValueError("capture and private control hashes differ")
        target = next(e for e in bundle.events if str(e.id) == truth["target_event_id"])
        healthy_cutoff = target.occurred_at - timedelta(microseconds=1)
        queries.append(
            dict(
                row=row,
                truth=truth,
                truth_sha256=digest(truth_path),
                healthy_cutoff=healthy_cutoff.isoformat(),
            )
        )
    dump(
        args.output / "frozen-inputs.json",
        dict(
            protocol_sha256=digest(args.protocol),
            index_sha256=digest(args.index),
            source_sha256=source_hashes,
            candidate_budget=24,
            result_budget=TOP_K,
            declared_at=datetime.now(UTC).isoformat(),
            queries=[
                dict(
                    episode_id=q["row"]["episode_id"],
                    symptom=q["row"]["symptom_service"],
                    analysis_cutoff=q["truth"]["intervals"]["analysis_end"],
                    healthy_cutoff=q["healthy_cutoff"],
                    truth_sha256=q["truth_sha256"],
                )
                for q in queries
            ],
        ),
    )
    rows = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=120, follow_redirects=False
    ) as client:
        for query in queries:
            row, truth = query["row"], query["truth"]
            episode = get(client, "/api/v1/episodes/" + row["episode_id"])
            snapshot = episode["active_snapshot_id"]
            if snapshot is None:
                raise ValueError("captured episode has no published snapshot")
            for regime, cutoff in (
                ("changed", truth["intervals"]["analysis_end"]),
                ("healthy", query["healthy_cutoff"]),
            ):
                scope = dict(
                    episode_id=row["episode_id"],
                    snapshot_id=snapshot,
                    as_of=cutoff,
                    temporal_mode="event_time_replay",
                )
                graph = GraphResponse.model_validate(get(client, "/api/v1/graph", scope))
                symptom = next(
                    n.service_id
                    for n in graph.nodes
                    if n.display_name.split("/")[-1] == row["symptom_service"]
                )
                payload = get(
                    client, "/api/v1/operations", {**scope, "symptom_service_id": symptom}
                )
                changes = ChangeReasoningResponse.model_validate(payload["change_reasoning"])
                dump(args.output / "responses" / f"{row['episode_id']}-{regime}.json", payload)
                predictions = methods(changes)
                future = [str(c.event_id) for c in changes.changes if c.occurred_at > dt(cutoff)]
                future.extend(
                    f["event"]["id"]
                    for f in payload["facts"]
                    if dt(f["event"]["occurred_at"]) > dt(cutoff)
                )
                citations = inspect_citations(client, payload, changes, scope)
                candidates = {str(c.event_id): c for c in changes.changes}
                target_id = truth["target_event_id"]
                target_key = next(
                    (c.service_id for c in changes.changes if str(c.event_id) == target_id), None
                )
                metrics = {}
                for name, predicted in predictions.items():
                    metrics[name] = dict(
                        top1_event_correct=(bool(predicted) and predicted[0] == target_id)
                        if regime == "changed"
                        else not predicted,
                        top1_service_correct=(
                            bool(predicted)
                            and target_key is not None
                            and candidates[predicted[0]].service_id == target_key
                        )
                        if regime == "changed"
                        else not predicted,
                        target_in_top3=target_id in predicted if regime == "changed" else None,
                        predictions=predicted,
                    )
                history = payload["history"]
                rows.append(
                    dict(
                        application_id=row["application_id"],
                        topology=row["topology"],
                        episode_id=row["episode_id"],
                        ordinal=row["ordinal"],
                        intervention=truth["intervention"],
                        regime=regime,
                        as_of=cutoff,
                        symptom_service=row["symptom_service"],
                        candidate_count=len(changes.changes),
                        target_candidate_recalled=target_id in candidates
                        if regime == "changed"
                        else None,
                        target_review_priority=any(
                            str(c.event_id) == target_id and c.priority == "review_first"
                            for c in changes.changes
                        )
                        if regime == "changed"
                        else None,
                        future_events=future,
                        metrics=metrics,
                        resolved_citation_count=len(citations),
                        resolved_citations=citations,
                        history_episode_ids=[m["episode_id"] for m in history["matches"]],
                        state_difference_count=sum(
                            len(m["state_differences"]) for m in history["matches"]
                        ),
                    )
                )
                dump(args.output / "cases" / f"{row['episode_id']}-{regime}.json", rows[-1])
                print(
                    json.dumps(
                        dict(
                            episode_id=row["episode_id"],
                            regime=regime,
                            candidates=len(candidates),
                            citations=len(citations),
                            graph_temporal=metrics["graph_temporal"],
                        )
                    ),
                    flush=True,
                )
    changed = [row for row in rows if row["regime"] == "changed"]
    healthy = [row for row in rows if row["regime"] == "healthy"]
    totals = dict(
        changed_cases=len(changed),
        healthy_cases=len(healthy),
        candidate_recall=sum(row["target_candidate_recalled"] for row in changed),
        review_priority_recall=sum(row["target_review_priority"] for row in changed),
        future_event_violations=sum(len(row["future_events"]) for row in rows),
        resolved_citations=sum(row["resolved_citation_count"] for row in rows),
        methods={
            name: dict(
                changed_top1_event=sum(
                    row["metrics"][name]["top1_event_correct"] for row in changed
                ),
                changed_top1_service=sum(
                    row["metrics"][name]["top1_service_correct"] for row in changed
                ),
                changed_recall_at3=sum(row["metrics"][name]["target_in_top3"] for row in changed),
                healthy_abstentions=sum(
                    row["metrics"][name]["top1_event_correct"] for row in healthy
                ),
            )
            for name in METHODS
        },
    )
    result = dict(
        protocol="operational-lab-v1",
        origin="captured_demo",
        source_sha256=source_hashes,
        protocol_sha256=digest(args.protocol),
        candidate_budget=24,
        output_budget=TOP_K,
        totals=totals,
        cases=rows,
        limitations=[
            "Six repeated local captures from three small authored applications; "
            "no production accuracy claim.",
            "Controlled intervention identity is an offline reference, "
            "not proof of general causal attribution.",
            "Same complete eligible change pool and output budget across methods; "
            "no learned tuning.",
            "Source-aware graph reasoning is evaluated separately from LLM prose generation.",
            "Prior-episode lookup has only one earlier episode per application; "
            "it does not measure scalable incident search.",
        ],
    )
    dump(args.output / "report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:14000")
    parser.add_argument("--index", type=Path, default=ROOT / "data/operational-lab/index.json")
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "reports/operational-lab/protocol.json"
    )
    parser.add_argument("--truth-root", type=Path, default=ROOT / "reports/operational-lab/private")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args)
    print(json.dumps(result["totals"], indent=2))
    if result["totals"]["future_event_violations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
