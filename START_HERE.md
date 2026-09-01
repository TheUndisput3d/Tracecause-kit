# Start here: run or develop TraceCause

TraceCause is implemented. Start with the [README](README.md) to run the complete
Docker application and explore the operational knowledge graph. The
[implementation status](docs/IMPLEMENTATION_STATUS.md),
[build log](docs/BUILD_LOG.md) and [release audit](docs/RELEASE_AUDIT.md) distinguish
verified behavior from remaining research and deployment limits.

The original implementation kit remains in this repository: the specification,
20 numbered phase prompts, component annexes and acceptance matrix explain the
engineering decisions and original gates. The instructions below describe that
original build sequence; they are not a request to restart completed phases.

For ongoing development, read `AGENTS.md`, the latest `docs/HANDOFF.md` and the
actual repository state first. Preserve frozen evaluations and immutable saved
results. Public hosting and GitHub pushes require the owner's explicit permission.

## Original phase-by-phase build workflow

Extract the kit into a new local repository named `tracecause`. Keep `AGENTS.md`, `docs/`, `prompts/`, `contracts/`, `configs/` and `examples/` at the repository root. Open that directory in the coding assistant. Do not paste the entire combined blueprint into every conversation; let the assistant read repository files.

Start with the following prompt:

```text
Read AGENTS.md, START_HERE.md, docs/SPEC.md,
docs/ACCEPTANCE_MATRIX.md and prompts/00-foundation.md.
Implement phase 00 only. Follow the phase's verification gates.
Do not fabricate successful checks or begin phase 01.
Report actual changes, commands/results and remaining blockers.
```

After phase 00 passes, use:

```text
Read AGENTS.md, docs/SPEC.md, docs/BUILD_LOG.md and
prompts/01-contracts-configuration.md. Inspect the previous phase's
verification evidence. Implement phase 01 only, run its tests,
and update the phase report and build log with observed results.
```

Continue in numeric order using the exact filenames in `prompts/`. Every phase prompt can also be copied into chat in full after the repository files are available. `prompts/NEXT_PHASE.md` advances exactly one verified phase without manually changing a filename. `prompts/REVIEW.md` provides an independent review after a phase; `prompts/RESUME.md` recovers a stopped session without losing context.

## What requires human action

Provide the machine/permissions required to run containers. Approve initial model downloads and local resource use before enabling Ollama. Approve deployment and external redistribution. Review the gold labels and benchmark claims. Confirm destructive operations separately. Do not give the coding assistant production credentials or unrestricted cloud access for this capstone.

The fixture-only path works without a paid model key after dependencies are installed. The local-model profile and actual telemetry capture are separate verification tracks. A no-network installation requires dependencies/images/model files to have been obtained beforehand; the kit does not bundle them.

## Reading order

`docs/SPEC.md` is the product/engineering source of truth, with the component annexes. `docs/DEPENDENCIES.md` is the package-level inventory. `docs/ACCEPTANCE_MATRIX.md` maps requirements to phases and tests. `AGENTS.md` sets persistent development rules. Numbered phase files contain the implementation tasks and completion gates. `contracts/` and `examples/` illustrate machine-readable boundaries. Phase 01 must extend and validate these starter contracts, not assume that every runtime field is already defined.

`docs/SOURCES.md` contains the primary documentation used to ground this plan, reviewed on 10 October 2026. Phase 00 must verify versions again at implementation time. `configs/defaults.yaml` contains proposed initial settings, not benchmarked optimal values.

## Milestones

Phases 00-09: real end-to-end CLI vertical slice with a temporal graph and template-generated evidence report.

Phases 10-13: local Ollama generation adapter, trained candidate ranker, captured demo data and fair evaluation.

Phases 14-19: API, polished dashboard, security/recovery checks, CI/performance, deployment and portfolio handoff.

The full project is complete only when actual gates pass. A small-sample or negative research result is not a failed engineering project; a fabricated result is.

The application runtime needs no paid model API. Codex is the implementation tool, not an application dependency; access to that tool may have its own cost. For an early usable demo, follow the MVP cut line in SPEC section 27.
