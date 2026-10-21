# ADR 0002: Local model configuration has resource limits, not a price ledger

Status: Accepted for Phase 01. Date: 2026-10-10.

`prompts/01-contracts-configuration.md` asks for a dated pricing configuration before local mode, while `docs/SPEC.md` sections 5 and 20, `docs/AI.md` and `docs/DEPENDENCIES.md` explicitly select local Ollama without a paid runtime provider or token-price ledger. The acceptance matrix requires validated config and mode distinctions, not a cost account. We resolve the conflict by requiring explicit operator opt-in, the reviewed Qwen tag and digest, an allowlisted local endpoint, one active generation, bounded context/output and deadline. We reject a fictional monetary price for local inference. Hardware/time use will be measured in later phases.

The configured local provider remains unavailable to public read-only mode. This ADR is a configuration decision, not proof of model download or inference.
