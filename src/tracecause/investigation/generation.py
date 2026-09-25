"""Pinned, bounded local narrative providers; evidence remains untrusted data."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.canonical import canonical_json, semantic_sha256
from tracecause.contracts.models import Narrative
from tracecause.investigation.planning import (
    PLAN_VERSION,
    ExplanationPlan,
    plan_input,
    plan_schema,
    render_plan,
)
from tracecause.investigation.slot import GenerationSlot, SlotError

PINNED_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
PINNED_MANIFEST_DIGEST = "sha256:0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
PINNED_TOKENIZER_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
PROMPT_PATH = Path("prompts/runtime/explain_v1.txt")
MAX_RESPONSE_BYTES = 64 * 1024


class GenerationError(ValueError):
    """A safe category that does not contain prompt or model output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GenerationOutput:
    narrative: Narrative
    prompt_tokens: int | None
    output_tokens: int | None
    duration_ms: float
    attempts: int
    plan: ExplanationPlan | None = None


class TokenCounter(Protocol):
    def count_messages(self, messages: list[dict[str, str]]) -> int: ...


class QwenTokenCounter:
    def __init__(self) -> None:
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen3-4B-Instruct-2507",
            revision=PINNED_TOKENIZER_REVISION,
            local_files_only=True,
            trust_remote_code=False,
        )

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        encoded: Any = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True
        )
        if isinstance(encoded, Mapping):
            encoded = encoded.get("input_ids")
        if not isinstance(encoded, list):
            raise GenerationError("tokenizer_output_invalid")
        return len(encoded)


class GenerationProvider(Protocol):
    def generate(self, bundle: dict[str, Any], template: Narrative) -> GenerationOutput: ...


class TemplateProvider:
    def generate(self, bundle: dict[str, Any], template: Narrative) -> GenerationOutput:
        return GenerationOutput(template, None, None, 0.0, 0)


def prompt_bytes(root: Path) -> bytes:
    path = root / PROMPT_PATH
    if path.is_symlink() or not path.is_file():
        raise GenerationError("prompt_missing")
    raw = path.read_bytes()
    if len(raw) > 10_000 or b"\x00" in raw:
        raise GenerationError("prompt_invalid")
    return raw


def generation_cache_hash(
    *,
    bundle: dict[str, Any],
    model_digest: str,
    settings: RuntimeSettings,
    prompt_hash: str,
) -> str:
    return semantic_sha256(
        {
            "bundle_hash": semantic_sha256(bundle),
            "model_digest": model_digest,
            "prompt_hash": prompt_hash,
            "generation_policy": PLAN_VERSION,
            "narrative_schema_hash": hashlib.sha256(
                canonical_json(Narrative.model_json_schema())
            ).hexdigest(),
            "settings": {
                "context": settings.generation_context_tokens,
                "input": settings.generation_input_tokens,
                "output": settings.generation_output_tokens,
                "deadline": settings.generation_deadline_seconds,
                "tokenizer": PINNED_TOKENIZER_REVISION,
                "temperature": 0.0,
            },
        }
    )


class LegacyOllamaProvider:
    """Historical full-report protocol retained for explicit offline comparisons."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        prompt_root: Path,
        slot_path: Path,
        client: httpx.Client | None = None,
        counter: TokenCounter | None = None,
    ) -> None:
        if (
            settings.ollama_model != PINNED_MODEL
            or settings.ollama_model_digest != PINNED_MANIFEST_DIGEST
            or settings.ollama_url not in {"http://ollama:11434", "http://127.0.0.1:11434"}
            or not settings.enable_local_model
            or settings.public_read_only
        ):
            raise GenerationError("model_configuration_invalid")
        self.settings = settings
        self.prompt = prompt_bytes(prompt_root).decode("utf-8")
        self.prompt_hash = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.slot_path = slot_path
        self.client = client or httpx.Client(base_url=settings.ollama_url, trust_env=False)
        self.counter = counter

    def capability(self) -> dict[str, Any]:
        try:
            response = self.client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
            models = response.json()["models"]
            found = next((item for item in models if item.get("name") == PINNED_MODEL), None)
            observed = found.get("digest") if isinstance(found, dict) else None
            digest_match = observed in {
                PINNED_MANIFEST_DIGEST,
                PINNED_MANIFEST_DIGEST.removeprefix("sha256:"),
            }
            return {
                "process_available": True,
                "model_available": found is not None,
                "digest_match": digest_match,
                "expected_digest": PINNED_MANIFEST_DIGEST,
                "observed_digest": f"sha256:{observed}"
                if observed and not observed.startswith("sha256:")
                else observed,
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {
                "process_available": False,
                "model_available": False,
                "digest_match": False,
                "expected_digest": PINNED_MANIFEST_DIGEST,
                "observed_digest": None,
            }

    def _post_bounded(self, payload: dict[str, Any], timeout: float) -> httpx.Response:
        """Bound wall time and bytes, including a peer that trickles its response.

        The daemon owns only transport work. It cannot clear the durable fence.
        A timeout leaves the fence set even if transport later finishes.
        """
        if timeout <= 0:
            raise GenerationError("generation_deadline")
        pending: Future[httpx.Response] = Future()

        def receive() -> None:
            try:
                with self.client.stream("POST", "/api/chat", json=payload, timeout=timeout) as wire:
                    wire.raise_for_status()
                    data = bytearray()
                    for chunk in wire.iter_bytes(chunk_size=4096):
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise GenerationError("ollama_output_too_long")
                    response = httpx.Response(wire.status_code, content=bytes(data))
                pending.set_result(response)
            except Exception as exc:
                pending.set_exception(exc)

        Thread(target=receive, name="bounded-model-transport", daemon=True).start()
        try:
            return pending.result(timeout=timeout)
        except FutureTimeout as exc:
            raise GenerationError("ollama_timeout") from exc

    def _chat(
        self,
        messages: list[dict[str, str]],
        remaining: float,
        output_limit: int,
        slot: GenerationSlot,
        schema: dict[str, Any] | None = None,
    ) -> tuple[str, int, int]:
        if remaining <= 0:
            raise GenerationError("generation_deadline")
        slot.mark_dispatch(self.settings.ollama_url)
        try:
            response = self._post_bounded(
                {
                    "model": PINNED_MODEL,
                    "messages": messages,
                    "format": schema if schema is not None else Narrative.model_json_schema(),
                    "stream": False,
                    "options": {
                        "num_ctx": self.settings.generation_context_tokens,
                        "num_predict": output_limit,
                        "temperature": 0.0,
                    },
                    "keep_alive": "5m",
                },
                timeout=remaining,
            )
        except httpx.TimeoutException as exc:
            raise GenerationError("ollama_timeout") from exc
        except httpx.HTTPError as exc:
            raise GenerationError("ollama_request_failed") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise GenerationError("ollama_output_too_long")
        try:
            payload = response.json()
            if payload.get("model") != PINNED_MODEL or payload.get("done") is not True:
                raise TypeError
            slot.complete()
            if payload.get("done_reason") not in {None, "stop"}:
                raise GenerationError("ollama_output_limit")
            content = payload["message"]["content"]
            prompt_tokens = payload["prompt_eval_count"]
            output_tokens = payload["eval_count"]
            if (
                not isinstance(content, str)
                or not isinstance(prompt_tokens, int)
                or not isinstance(output_tokens, int)
                or prompt_tokens < 0
                or output_tokens < 0
            ):
                raise TypeError
        except GenerationError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GenerationError("ollama_response_invalid") from exc
        if output_tokens > output_limit:
            raise GenerationError("ollama_output_limit")
        return content, prompt_tokens, output_tokens

    def probe_schema(self) -> dict[str, Any]:
        capability = self.capability()
        if not capability["digest_match"]:
            return {**capability, "schema_supported": False}
        try:
            with GenerationSlot(self.slot_path) as slot:
                slot.mark_dispatch(self.settings.ollama_url)
                response = self._post_bounded(
                    {
                        "model": PINNED_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": "Return a JSON object with ready set to true.",
                            }
                        ],
                        "format": {
                            "type": "object",
                            "properties": {"ready": {"type": "boolean", "const": True}},
                            "required": ["ready"],
                            "additionalProperties": False,
                        },
                        "stream": False,
                        "options": {"num_ctx": 1024, "num_predict": 32, "temperature": 0.0},
                    },
                    timeout=self.settings.generation_deadline_seconds,
                )
                payload = response.json()
                if payload.get("done") is not True or payload.get("model") != PINNED_MODEL:
                    raise ValueError("nonterminal provider response")
                slot.complete()
                content = payload["message"]["content"]
                supported = json.loads(content) == {"ready": True}
                return {**capability, "schema_supported": supported}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {**capability, "schema_supported": False}

    def generate(self, bundle: dict[str, Any], template: Narrative) -> GenerationOutput:
        started = time.monotonic()
        deadline = started + self.settings.generation_deadline_seconds
        capability = self.capability()
        if not capability["process_available"]:
            raise GenerationError("ollama_unavailable")
        if not capability["model_available"]:
            raise GenerationError("ollama_model_missing")
        if not capability["digest_match"]:
            raise GenerationError("ollama_digest_mismatch")
        user_content = canonical_json(bundle).decode("utf-8")
        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": user_content},
        ]
        if self.counter is None:
            try:
                self.counter = QwenTokenCounter()
            except (OSError, ValueError) as exc:
                raise GenerationError("tokenizer_missing") from exc
        input_tokens = self.counter.count_messages(messages)
        if input_tokens > self.settings.generation_input_tokens:
            raise GenerationError("generation_input_limit")
        if (
            input_tokens + self.settings.generation_output_tokens
            > self.settings.generation_context_tokens
        ):
            raise GenerationError("generation_context_limit")
        try:
            with GenerationSlot(self.slot_path) as slot:
                attempts = 0
                total_prompt = 0
                total_output = 0
                while True:
                    attempts += 1
                    content, prompt_count, output_count = self._chat(
                        messages,
                        deadline - time.monotonic(),
                        self.settings.generation_output_tokens - total_output,
                        slot,
                    )
                    total_prompt += prompt_count
                    total_output += output_count
                    try:
                        narrative = Narrative.model_validate_json(content)
                        return GenerationOutput(
                            narrative,
                            total_prompt,
                            total_output,
                            round((time.monotonic() - started) * 1000, 3),
                            attempts,
                        )
                    except ValidationError as exc:
                        if attempts > 1 or total_output >= self.settings.generation_output_tokens:
                            raise GenerationError("ollama_schema_invalid") from exc
                        messages = [
                            *messages,
                            {
                                "role": "user",
                                "content": (
                                    "The previous response failed the JSON schema. "
                                    "Return only valid "
                                    "JSON matching the same schema and supplied bundle. "
                                    "Do not add facts or IDs."
                                ),
                            },
                        ]
                        if (
                            self.counter.count_messages(messages)
                            > self.settings.generation_input_tokens
                        ):
                            raise GenerationError("generation_input_limit") from exc
        except SlotError as exc:
            raise GenerationError(exc.code) from exc


class OllamaProvider(LegacyOllamaProvider):
    """Production constrained plan generation with independent deterministic compilation."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        prompt_root: Path,
        slot_path: Path,
        client: httpx.Client | None = None,
        counter: TokenCounter | None = None,
    ) -> None:
        super().__init__(
            settings, prompt_root=prompt_root, slot_path=slot_path, client=client, counter=counter
        )
        path = prompt_root / "prompts/runtime/plan_v1.txt"
        if path.is_symlink() or not path.is_file():
            raise GenerationError("prompt_missing")
        raw = path.read_bytes()
        if len(raw) > 10_000 or b"\x00" in raw:
            raise GenerationError("prompt_invalid")
        self.prompt = raw.decode("utf-8")
        self.prompt_hash = hashlib.sha256(raw).hexdigest()

    def generate(self, bundle: dict[str, Any], template: Narrative) -> GenerationOutput:
        started = time.monotonic()
        deadline = started + self.settings.generation_deadline_seconds
        capability = self.capability()
        for key, code in (
            ("process_available", "ollama_unavailable"),
            ("model_available", "ollama_model_missing"),
            ("digest_match", "ollama_digest_mismatch"),
        ):
            if not capability[key]:
                raise GenerationError(code)
        messages = [
            {"role": "system", "content": self.prompt},
            {
                "role": "user",
                "content": canonical_json(
                    plan_input(str(bundle.get("question", "")), template)
                ).decode(),
            },
        ]
        if self.counter is None:
            try:
                self.counter = QwenTokenCounter()
            except (OSError, ValueError) as exc:
                raise GenerationError("tokenizer_missing") from exc
        output_budget = min(192, self.settings.generation_output_tokens)
        total_prompt = total_output = 0
        try:
            with GenerationSlot(self.slot_path) as slot:
                for attempt in (1, 2):
                    tokens = self.counter.count_messages(messages)
                    if tokens > self.settings.generation_input_tokens:
                        raise GenerationError("generation_input_limit")
                    remaining_output = output_budget - total_output
                    if tokens + remaining_output > self.settings.generation_context_tokens:
                        raise GenerationError("generation_context_limit")
                    content, prompt_count, output_count = self._chat(
                        messages,
                        deadline - time.monotonic(),
                        remaining_output,
                        slot,
                        schema=plan_schema(template, str(bundle.get("question", ""))),
                    )
                    total_prompt += prompt_count
                    total_output += output_count
                    try:
                        plan = ExplanationPlan.model_validate_json(content)
                        narrative = render_plan(plan, template, str(bundle.get("question", "")))
                    except ValueError as exc:
                        if attempt == 2 or total_output >= output_budget:
                            raise GenerationError("ollama_plan_invalid") from exc
                        # No rejected text is reflected into the trusted instruction channel.
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "The plan failed validation. Use distinct catalog fact IDs; "
                                    "the first fact must match the focus. Return only the plan."
                                ),
                            }
                        )
                        continue
                    return GenerationOutput(
                        narrative,
                        total_prompt,
                        total_output,
                        round((time.monotonic() - started) * 1000, 3),
                        attempt,
                        plan,
                    )
        except SlotError as exc:
            raise GenerationError(exc.code) from exc
        raise GenerationError("ollama_plan_invalid")


def safe_bundle(
    *,
    question: str,
    status: str,
    cutoff: str,
    temporal_mode: str,
    ranked_scores: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    allowed_checks: list[str],
    limitations: list[str],
    draft_narrative: dict[str, Any],
) -> dict[str, Any]:
    """Only redacted, eligible typed items enter the local model boundary."""
    claimed_metrics = {
        canonical_json(claim["metric"])
        for claim in draft_narrative["claims"]
        if claim["claim_type"] == "numeric" and claim["metric"] is not None
    }
    compact_evidence = [
        {
            "evidence_id": item["evidence_id"],
            "kind": item["kind"],
            "text": item["text"],
            "interval": item["interval"],
            "published_at": item["published_at"],
            "metrics": [
                metric for metric in item["metrics"] if canonical_json(metric) in claimed_metrics
            ],
            "quality_flags": item["quality_flags"],
        }
        for item in evidence
    ]
    compact_paths = [
        {
            "path_id": path["path_id"],
            "service_ids": path["service_ids"],
            "edges": [
                {
                    "source_service_id": edge["source_service_id"],
                    "target_service_id": edge["target_service_id"],
                    "supporting_evidence_ids": edge["supporting_evidence_ids"],
                }
                for edge in path["edges"]
            ],
        }
        for path in paths
    ]
    return {
        "task": (
            "Copy draft_narrative as the JSON response. You may shorten only summary.text "
            "to one generic sentence without numbers, service names, commands or new facts. "
            "Copy all claims and every other field and value exactly, including status, "
            "candidate order, IDs, citations, metrics, paths and limitations. "
            "Use compact JSON with no indentation. If uncertain, copy the entire draft."
        ),
        "question": question,
        "server_status": status,
        "cutoff": cutoff,
        "temporal_mode": temporal_mode,
        "fixed_ranked_scores": ranked_scores,
        "eligible_evidence": compact_evidence,
        "validated_paths": compact_paths,
        "allowed_read_only_check_ids": allowed_checks,
        "limitations": limitations,
        "draft_narrative": draft_narrative,
    }
