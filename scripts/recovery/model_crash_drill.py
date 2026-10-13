"""Actual pinned-model crash drill against the separately owned diagnostic container.

A test-only client points to its loopback port. Production endpoint allowlists
stay unchanged. A wire trace checkpoint proves the chat body was sent before
SIGKILL; the durable gate must refuse all subsequent chat dispatches.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from scripts.recovery.reconcile_generation import reconcile
from tracecause.config.settings import RuntimeSettings
from tracecause.contracts.models import GenerationMode
from tracecause.investigation.generation import (
    PINNED_MANIFEST_DIGEST,
    GenerationError,
    OllamaProvider,
)
from tracecause.investigation.templates import template_narrative

ROOT = Path.cwd()
OUTPUT = ROOT / "reports/phase16-model-crash-v1"
CONTAINER = "tracecause-recovery-model-v1-ollama-1"


def provider(*, checkpoint: bool = False) -> OllamaProvider:
    settings = RuntimeSettings(
        public_read_only=False,
        operator_token=SecretStr("local-diagnostic-only"),
        enable_local_model=True,
        generation_provider=GenerationMode.OLLAMA,
        ollama_model_digest=PINNED_MANIFEST_DIGEST,
    )

    def attach(request: httpx.Request) -> None:
        if checkpoint and request.url.path == "/api/chat":

            def trace(name: str, details: dict) -> None:
                if name == "http11.send_request_body.complete":
                    (OUTPUT / "chat-dispatched").touch()

            request.extensions["trace"] = trace

    client = httpx.Client(
        base_url="http://127.0.0.1:21434", trust_env=False, event_hooks={"request": [attach]}
    )
    return OllamaProvider(
        settings, prompt_root=ROOT, slot_path=OUTPUT / "generation.lock", client=client
    )


def main() -> None:
    template = template_narrative("insufficient_evidence", [], {}, {}, {}, {}, [])
    bundle = {"draft_narrative": template.model_dump(mode="json")}
    if "--child" in sys.argv:
        provider(checkpoint=True).generate(bundle, template)
        return
    OUTPUT.mkdir(mode=0o700)
    (OUTPUT / "provider-project").write_text("tracecause-recovery-model-v1\n")
    original = provider()
    for _ in range(30):
        if original.capability()["digest_match"]:
            break
        time.sleep(1)
    assert original.capability()["digest_match"], "diagnostic provider/model unavailable"
    with (OUTPUT / "child.log").open("w") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "scripts.recovery.model_crash_drill", "--child"],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(300):
                if (OUTPUT / "chat-dispatched").exists():
                    break
                assert child.poll() is None, "child exited before actual dispatch"
                time.sleep(0.1)
            assert (OUTPUT / "chat-dispatched").exists()
            child.kill()
            child.wait(timeout=5)
            assert child.returncode == -9
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    restarted = provider()
    try:
        restarted.generate(bundle, template)
    except GenerationError as exc:
        assert exc.code == "generation_reconciliation_required"
    else:
        raise AssertionError("upstream concurrency admitted after worker death")
    # Restart only this owned diagnostic provider, never the native host service.
    subprocess.run(["docker", "restart", CONTAINER], check=True, capture_output=True, timeout=45)
    for _ in range(30):
        if restarted.capability()["digest_match"]:
            break
        time.sleep(1)
    receipt = reconcile(OUTPUT / "generation.lock", CONTAINER)
    generated = restarted.generate(bundle, template)
    assert generated.narrative == template
    report = {
        "actual_model": True,
        "diagnostic_runtime": "0.40.3",
        "release_runtime_test": False,
        "digest": PINNED_MANIFEST_DIGEST,
        "child_exit": child.returncode,
        "wire_body_sent_before_kill": True,
        "second_chat_blocked": True,
        "restart_receipt": receipt,
        "post_reconciliation_generation": {
            "prompt_tokens": generated.prompt_tokens,
            "output_tokens": generated.output_tokens,
            "duration_ms": generated.duration_ms,
            "attempts": generated.attempts,
        },
    }
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
