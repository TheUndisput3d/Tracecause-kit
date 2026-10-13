"""Clear a generation fence only after inspecting a dedicated container restart.

Never restarts a provider itself. The operator first stops workers and restarts
that deployment's Ollama container. Native providers remain fenced until handled
by an equivalent process-identity check; deleting lock files is not recovery.
"""

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tracecause.investigation.slot import GenerationSlot, SlotError


def reconcile(path: Path, container: str) -> dict[str, object]:
    with GenerationSlot(path, recovery=True) as slot:
        state = slot.state()
        inspected = subprocess.run(
            ["docker", "inspect", container], check=True, capture_output=True, text=True, timeout=10
        )
        row = json.loads(inspected.stdout)[0]
        labels = row["Config"].get("Labels") or {}
        # This command is for a container deployment with the canonical DNS name.
        # A restarted unrelated/native provider cannot clear that deployment's fence.
        if (
            state.get("provider") != "http://ollama:11434"
            or labels.get("com.docker.compose.service") != "ollama"
            or not row["State"]["Running"]
            or datetime.fromisoformat(row["State"]["StartedAt"])
            <= datetime.fromisoformat(state["started_at"])
        ):
            raise SlotError("provider_restart_not_verified")
        expected_project = path.parent / "provider-project"
        if (
            expected_project.is_symlink()
            or not expected_project.is_file()
            or expected_project.read_text().strip() != labels.get("com.docker.compose.project")
        ):
            raise SlotError("provider_project_not_verified")
        receipt = {
            "provider": state["provider"],
            "interrupted_at": state["started_at"],
            "container_id": row["Id"],
            "provider_started_at": row["State"]["StartedAt"],
            "verification": "docker_state_and_deployment_project",
        }
        # Persist a receipt before clearing. A receipt alone never opens admission.
        receipt_path = path.with_name(path.name + f".restart-{uuid4()}.json")
        with receipt_path.open("x") as output:
            json.dump(receipt, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        slot.complete()
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot-path", type=Path, required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.slot_path, args.container), indent=2))


if __name__ == "__main__":
    main()
