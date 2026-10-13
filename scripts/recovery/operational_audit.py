"""Reopen operational proofs and record deterministic state for a restore drill."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from tracecause.api import reads as core_reads
from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import TemporalMode
from tracecause.operational import reads
from tracecause.storage.schema import EpisodeRow, InvestigationRow, SnapshotRow


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def audit() -> dict:
    resources = open_resources(load_settings(Path("configs/defaults.yaml")))
    try:
        with resources.factory() as session:
            episodes = session.scalars(select(EpisodeRow).order_by(EpisodeRow.id)).all()
            snapshots = session.scalars(select(SnapshotRow).order_by(SnapshotRow.id)).all()
            saved = session.scalars(select(InvestigationRow).order_by(InvestigationRow.id)).all()
        state = {
            "active_snapshots": {str(row.id): str(row.active_snapshot_id) for row in episodes},
            "snapshots": {
                str(row.id): {
                    "episode_id": str(row.episode_id),
                    "status": row.status,
                    "content_hash": row.content_hash,
                    "membership": row.membership,
                }
                for row in snapshots
            },
            "saved_result_hashes": {str(row.id): row.result_artifact_hash for row in saved},
            "operational_views": {},
        }
        for episode in episodes:
            if not episode.data_quality.get("operational_application_id"):
                continue
            if episode.active_snapshot_id is None:
                raise ValueError("operational audit requires a published snapshot")
            cursor = datetime.fromisoformat(episode.data_quality["default_as_of"])
            selected = episode.data_quality["default_symptom_service_id"]
            result = reads.operations(
                resources,
                episode.id,
                episode.active_snapshot_id,
                cursor,
                TemporalMode.EVENT,
                selected,
            )
            if not result.change_reasoning or not any(
                change.signal for change in result.change_reasoning.changes
            ):
                raise ValueError("operational aggregate evidence is absent at the public cursor")
            proofs: set[tuple[str, UUID, UUID, datetime, TemporalMode]] = set()

            def add(
                identifier,
                eid=episode.id,
                sid=episode.active_snapshot_id,
                at=cursor,
                mode=TemporalMode.EVENT,
                collected=proofs,
            ):
                collected.add((identifier, eid, sid, at, mode))

            for fact in result.facts:
                add(fact.evidence_id)
            for edge in result.graph.edges:
                add(edge.supporting_evidence_ids[0])
            for change in result.change_reasoning.changes:
                if change.signal:
                    add(change.signal.observation_evidence_id)
                    if change.signal.baseline_evidence_ids:
                        add(change.signal.baseline_evidence_ids[0])
            for match in result.history.matches:
                references = [*match.matching_signal_evidence, *match.current_signal_evidence]
                for difference in match.state_differences:
                    references.extend(
                        ref
                        for ref in (difference.previous_evidence, difference.current_evidence)
                        if ref is not None
                    )
                for ref in references:
                    add(
                        ref.evidence_id,
                        ref.episode_id,
                        ref.snapshot_id,
                        ref.as_of,
                        ref.temporal_mode,
                    )
            opened_hashes = []
            for identifier, eid, sid, at, mode in sorted(
                proofs, key=lambda value: tuple(str(part) for part in value)
            ):
                if identifier.startswith("operation:"):
                    opened = reads.evidence(resources, identifier, eid, sid, at, mode)
                elif identifier.startswith("handoff:"):
                    opened = reads.handoff_source(resources, identifier, eid, sid, at, mode)
                else:
                    opened = core_reads.evidence(resources, identifier, sid, at, mode)
                opened_hashes.append(digest(opened.model_dump(mode="json")))
            state["operational_views"][str(episode.id)] = {
                "application_id": result.application_id,
                "response_sha256": digest(result.model_dump(mode="json")),
                "facts": len(result.facts),
                "history_matches": len(result.history.matches),
                "message_edges": sum(
                    edge.relationship == "MESSAGE_FLOW" for edge in result.graph.edges
                ),
                "proofs_opened": len(proofs),
                "proofs_sha256": digest(sorted(opened_hashes)),
            }
            print(
                json.dumps({"operational_proofs_verified": str(episode.id), "count": len(proofs)}),
                flush=True,
            )
        if len(state["operational_views"]) != 6:
            raise ValueError("isolated drill requires exactly the six bundled operational episodes")
        if sum(value["message_edges"] for value in state["operational_views"].values()) < 2:
            raise ValueError("isolated drill did not verify both asynchronous episodes")
        if sum(value["history_matches"] for value in state["operational_views"].values()) < 3:
            raise ValueError("isolated drill did not verify all three historical comparisons")
        return state
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = audit()
    with args.report.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
