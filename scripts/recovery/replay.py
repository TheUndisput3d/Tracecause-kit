"""Reopen every restored saved citation, then run one fresh template investigation."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from tracecause.api.resources import open_resources
from tracecause.config.settings import load_settings
from tracecause.contracts.models import (
    GenerationMode,
    InvestigationRequest,
    RankingMode,
    RetrievalMode,
)
from tracecause.storage.schema import InvestigationRow


def replay() -> dict[str, object]:
    resources = open_resources(load_settings(Path("configs/defaults.yaml")))
    try:
        with resources.factory() as session:
            saved = session.scalars(select(InvestigationRow).order_by(InvestigationRow.id)).all()
        if not saved:
            raise ValueError("restore drill requires saved investigations")
        citations = 0
        service = resources.investigations
        for row in saved:
            result = service.show(row.workspace_id, row.id)
            for evidence in result.evidence:
                opened = service.evidence(row.workspace_id, row.id, evidence.evidence_id)
                if opened["evidence"]["text"] != evidence.text:
                    raise ValueError("restored citation differs")
                citations += 1
        request = InvestigationRequest(
            episode_id=result.episode_id,
            snapshot_id=result.snapshot_id,
            as_of=result.as_of,
            temporal_mode=result.temporal_mode,
            question="Restore drill: review eligible observations.",
            retrieval_mode=RetrievalMode.LEXICAL,
            ranking_mode=RankingMode.HEURISTIC,
            generation_mode=GenerationMode.TEMPLATE,
        )
        new = service.investigate(result.workspace_id, request)
        for evidence in new.evidence:
            service.evidence(new.workspace_id, new.investigation_id, evidence.evidence_id)
        return {
            "saved_investigations_replayed": len(saved),
            "saved_citations_resolved": citations,
            "new_investigation_id": str(new.investigation_id),
            "new_status": new.narrative.status,
            "new_citations_resolved": len(new.evidence),
            "is_replayed": new.is_replayed,
            "frozen_benchmark_rerun": False,
        }
    finally:
        resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = replay()
    with args.report.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
