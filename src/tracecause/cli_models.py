"""Explicit local model prefetch and independent capability probing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.models import GenerationMode
from tracecause.investigation.generation import (
    PINNED_MANIFEST_DIGEST,
    PINNED_MODEL,
    PINNED_TOKENIZER_REVISION,
    OllamaProvider,
)


def add_model_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    models = subparsers.add_parser("models", help="prefetch and probe the pinned local model")
    actions = models.add_subparsers(dest="models_action", required=True)
    for name in ("pull", "probe"):
        command = actions.add_parser(name)
        command.add_argument("--ollama-url", default="http://127.0.0.1:11434")
        command.add_argument(
            "--artifact-root",
            type=Path,
            required=True,
            help="Same artifact root as the worker; shares generation admission",
        )
        command.add_argument(
            "--operator-token-file", type=Path, default=Path(".secrets/operator_token")
        )


def _settings(args: argparse.Namespace) -> RuntimeSettings:
    token_path = args.operator_token_file
    if token_path.is_symlink() or not token_path.is_file():
        raise ValueError("operator token file is missing or unsafe")
    token = token_path.read_text().strip()
    if not token:
        raise ValueError("operator token is empty")
    return RuntimeSettings(
        public_read_only=False,
        generation_provider=GenerationMode.OLLAMA,
        enable_local_model=True,
        operator_token=SecretStr(token),
        ollama_model_digest=PINNED_MANIFEST_DIGEST,
        ollama_url=args.ollama_url,
    )


def run_models(args: argparse.Namespace) -> int:
    try:
        settings = _settings(args)
        if args.models_action == "pull":
            with httpx.Client(base_url=settings.ollama_url, trust_env=False) as client:
                response = client.post(
                    "/api/pull",
                    json={"model": PINNED_MODEL, "stream": False},
                    timeout=3600.0,
                )
                response.raise_for_status()
            from huggingface_hub import snapshot_download

            snapshot_download(
                "Qwen/Qwen3-4B-Instruct-2507",
                revision=PINNED_TOKENIZER_REVISION,
                allow_patterns=[
                    "config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "special_tokens_map.json",
                ],
            )
            output: dict[str, Any] = {"model_prefetched": True, "tokenizer_prefetched": True}
        elif args.models_action == "probe":
            provider = OllamaProvider(
                settings,
                prompt_root=Path.cwd(),
                slot_path=args.artifact_root.absolute() / "generation.lock",
            )
            output = provider.probe_schema()
            if not output["schema_supported"]:
                print(json.dumps(output, sort_keys=True), file=sys.stderr)
                return 2
        else:
            raise ValueError("unsupported model command")
        print(json.dumps(output, sort_keys=True))
        return 0
    except (ValueError, OSError, httpx.HTTPError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
