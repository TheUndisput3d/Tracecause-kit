"""Write the frozen, authored TraceCause demonstration document corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "runbooks" / "demo"

RUNBOOKS = (
    (
        "Storefront request overview",
        "storefront",
        "A slow customer request can reflect time spent in a downstream hop or local rendering.",
        "Compare the storefront SERVER span with its child CLIENT span before "
        "assigning responsibility.",
        "A long parent span alone does not identify the slow child.",
    ),
    (
        "Checkout timing checklist",
        "checkout",
        "Checkout can show high latency while a dependency remains healthy.",
        "Compare SERVER duration, outgoing CLIENT duration and completion status "
        "for the same trace.",
        "Retries and queuing can lengthen checkout without a slower dependency.",
    ),
    (
        "Payment response triage",
        "payment",
        "Payment responses may have explicit HTTP errors or an upstream timeout.",
        "Separate payment SERVER status from checkout CLIENT status and inspect matched trace IDs.",
        "An upstream timeout does not prove that payment itself failed.",
    ),
    (
        "Catalog read diagnostics",
        "catalog",
        "Catalog reads can become sparse or slow during normal inventory changes.",
        "Inspect request counts, status and dependency timing in matching ten-second buckets.",
        "A low sample count should be shown as missingness, not certainty.",
    ),
    (
        "Inventory latency checklist",
        "inventory",
        "Inventory responses may be delayed even when the caller returns success.",
        "Compare inventory SERVER p50 and p95 with its own baseline and call support.",
        "A one-off tail sample may not represent a sustained incident.",
    ),
    (
        "Storefront edge retry note",
        "front-end",
        "A repeated edge request can inflate volume without changing downstream health.",
        "Count unique SERVER spans and check whether CLIENT attempts share one user request.",
        "The term front-end is a curated alias only within the demo namespace.",
    ),
    (
        "Order flow dependency map",
        "orders",
        "The order path includes several independently observed service calls.",
        "Walk supported caller-to-callee edges and inspect each observation end time.",
        "A graph path is a communication path, not a root-cause chain.",
    ),
    (
        "Payment gateway alias guide",
        "pay-gateway",
        "A gateway label may refer to a client adapter or the payment service.",
        "Resolve the curated service tuple before using this note as a citation.",
        "Name similarity alone is insufficient across environments.",
    ),
    (
        "Product list cache checks",
        "product-list",
        "Cached catalog content can be stale while request latency remains normal.",
        "Inspect catalog status and compare responses with observed inventory calls.",
        "Stale content is not established by latency telemetry alone.",
    ),
    (
        "Stock service alias guide",
        "stock",
        "Stock is an authored shorthand for inventory in this demo only.",
        "Verify the namespace and environment before accepting a stock mention.",
        "This alias is not a universal service identity.",
    ),
    (
        "HTTP error interpretation",
        "checkout",
        "HTTP 4xx and 5xx counts carry different operational meanings.",
        "Compare explicit span ERROR status with supported HTTP response codes.",
        "Do not treat every HTTP 4xx response as a service failure.",
    ),
    (
        "Sparse traffic interpretation",
        "catalog",
        "Short windows with few calls can produce unstable percentile estimates.",
        "Inspect sample counts, baseline bucket coverage and quality flags before ranking.",
        "Sparse traffic can hide a real issue or exaggerate a single outlier.",
    ),
    (
        "Trace parent validation",
        "storefront",
        "A shared trace ID can include several branches that are not direct calls.",
        "Confirm parent-span ancestry and caller-to-callee direction for each dependency.",
        "A shared trace ID alone does not establish a CALLS edge.",
    ),
    (
        "Asynchronous work caveat",
        "checkout",
        "Queue work may continue after a request has completed.",
        "Separate asynchronous links from synchronous parent-child spans.",
        "The first graph projection does not infer queue or async dependencies.",
    ),
    (
        "Baseline comparison note",
        "inventory",
        "A quiet baseline can make a small absolute change appear large.",
        "Review the floor applied to latency and volume deviation scores.",
        "A standardized deviation is not a probability of failure.",
    ),
    (
        "Incident onset review",
        "payment",
        "An observed symptom may begin after the underlying change.",
        "Compare first anomalous buckets across services using the same cutoff.",
        "Ordering alone does not prove the underlying cause.",
    ),
    (
        "Conflicting runbook guidance",
        "catalog",
        "An older operational note recommends restarting catalog after any slow read.",
        "Check recent trace evidence and inventory timing before any action.",
        "The older recommendation is intentionally preserved as conflicting demo guidance, "
        "not an automatic remediation.",
    ),
    (
        "Unrelated maintenance reminder",
        "storefront",
        "Routine documentation review may be scheduled during an incident window.",
        "Keep unrelated maintenance notes separate from trace-supported hypotheses.",
        "Coincidental publication time is not incident evidence.",
    ),
)

HISTORY = (
    (
        "Historical note on a payment timeout",
        "payment",
        "A previous demo incident was attributed by its author to a short caller timeout.",
        "Compare caller timeout configuration with current SERVER completion before "
        "reusing that explanation.",
        "The old report is not proof about another episode.",
    ),
    (
        "Historical note on catalog latency",
        "catalog",
        "A prior simulated event was reported as slow catalog cache refresh.",
        "Check the current catalog SERVER spans and sample count independently.",
        "The historical attribution may be incomplete.",
    ),
    (
        "Historical note on inventory lag",
        "inventory",
        "One archived exercise described inventory lag during a busy period.",
        "Check current inventory durations and throughput before forming a hypothesis.",
        "The archived exercise is not a current observation.",
    ),
    (
        "Historical note on checkout retries",
        "checkout",
        "An earlier demo author reported retry amplification at checkout.",
        "Compare current unique trace IDs and CLIENT attempt counts.",
        "Retries can also be an effect of a different problem.",
    ),
    (
        "Historical note on storefront rendering",
        "storefront",
        "A past demonstration described local rendering overhead at storefront.",
        "Compare current storefront SERVER time with supported downstream timings.",
        "The same visible symptom can arise from a different service.",
    ),
    (
        "Historical note with uncertain cause",
        "payment",
        "A retired note guessed that payment caused a delay without span support.",
        "Treat that guess as an unverified historical assertion and inspect current trace support.",
        "This deliberately weak note must not become a telemetry edge.",
    ),
)

ALIASES = {
    "front-end": "storefront",
    "orders": "checkout",
    "pay-gateway": "payment",
    "product-list": "catalog",
    "stock": "inventory",
}


def _document(index: int, row: tuple[str, str, str, str, str], historical: bool) -> str:
    title, mention, symptom, check, caveat = row
    source_id = f"demo-doc-{index:02d}"
    publication = "2026-02-01T00:00:00Z" if historical else "2025-12-01T00:00:00Z"
    doc_type = "historical_note" if historical else "runbook"
    curated = ""
    if mention in ALIASES:
        curated = (
            "curated_service_refs:\n"
            "  - namespace: minishop\n"
            "    environment: demo\n"
            f"    name: {ALIASES[mention]}\n"
            f"    alias: {mention}\n"
        )
    predicate = "Historical report" if historical else "Symptom"
    return (
        f"---\nsource_id: {source_id}\nversion: '1.0'\ntitle: {title}\n"
        f"origin: authored_demo\ndocument_type: {doc_type}\n"
        f"published_at: {publication}\nlicense: Apache-2.0\n"
        f"attribution: TraceCause authored demonstration corpus\n{curated}---\n"
        f"# {title}\n\n"
        f"This authored demonstration note discusses {mention} in the minishop demo. "
        "It is guidance or a historical report, not a measurement of the current episode.\n\n"
        f"## Observation or report\n\n**{predicate}:** {symptom}\n\n"
        f"## Diagnostic check\n\n**Check:** {check}\n\n"
        f"## Limits\n\n{caveat}\n"
    )


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = (*((row, False) for row in RUNBOOKS), *((row, True) for row in HISTORY))
    manifest: list[dict[str, str | int]] = []
    for index, (row, historical) in enumerate(rows, start=1):
        content = _document(index, row, historical).encode()
        path = ROOT / f"demo-doc-{index:02d}.md"
        path.write_bytes(content)
        manifest.append(
            {
                "source_id": f"demo-doc-{index:02d}",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "kind": "historical_note" if historical else "runbook",
            }
        )
    payload = {"schema_version": "1.0", "origin": "authored_demo", "documents": manifest}
    (ROOT / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
