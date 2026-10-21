# ADR 0009: Captured replay boundaries and recoverable collection

Date: 2026-10-11. Status: accepted for Phase 12 implementation; phase acceptance pending.

## Context

Real capture stages end between signal buckets. Rounding baseline end upward includes early intervention data in the baseline. An interactive process also failed to survive a chat interruption during the fixed, multi-hour capture. Public export files do not contain trustworthy historical span arrival times.

## Decisions

- Floor baseline end to the last closed ten-second boundary. Exclude the partial bucket from baseline estimation; it can enter analysis as observed data. Keep insufficient-baseline flags when fewer than the required baseline samples exist.
- Repair existing episode analysis boundaries on idempotent import, clearing the active snapshot pointer. Rebuild from authoritative spans; retain old immutable snapshots and reports. Do not project derived signals computed with an obsolete baseline boundary.
- Require complete per-span availability within the scoped imported episode before available-time replay. An empty event selection cannot establish that capability. Export file receipt time never substitutes for span availability.
- Freeze the 24-run schedule and validate hashes of completed sources before resume. Retain incomplete raw exports without counting them. Hold an exclusive process lock for capture/control mutations and publish atomic stage progress.
- Run the long local capture under a user systemd service so a chat interruption does not terminate it. Preserve actual source/image provenance at resume. Never claim that this reconstructs an unsaved original harness snapshot.
- Keep private injector truth and review sheets outside runtime inputs. Human reviewers must confirm mapping, observed effects, identifiability and target sets. A validator checks the sheet structure and attestation, not the reviewer's identity.

## Consequences

Baseline duration loses at most one partial bucket; completed full runs still provide many baseline buckets. Historical reports remain available but outputs based on the old boundary are superseded. Capture cannot be reset concurrently through its CLI. Human review and the remaining collection remain real completion gates. No production generalization or zero-export-loss claim follows from the observed toy application traces.
