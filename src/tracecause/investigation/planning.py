"""Evidence-plan compiler: the model selects facts; code owns factual rendering."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecause.contracts.models import Narrative, Summary

PLAN_VERSION = "evidence-plan-v1"
_OUTSIDE_READ_ONLY = re.compile(
    r"\b(?:sudo|kubectl|curl|execute|executed)\b|"
    r"ignore.{0,40}(?:instructions|system)|"
    r"(?:change|alter|replace|fabricate).{0,35}(?:count|metric|fact|citation)",
    re.IGNORECASE,
)


class ExplanationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    focus: Literal["overview", "measurements", "dependencies", "limitations"]
    fact_ids: list[str] = Field(max_length=3)


def fact_catalog(template: Narrative) -> dict[str, dict[str, Any]]:
    return {
        f"f{index}": {
            "kind": claim.claim_type,
            "description": claim.text,
            "metric": claim.metric.model_dump(mode="json") if claim.metric else None,
            "path_id": claim.path_id,
            "evidence_ids": claim.evidence_ids,
        }
        for index, claim in enumerate(template.claims)
    }


def plan_schema(template: Narrative, question: str = "") -> dict[str, Any]:
    catalog = fact_catalog(template)
    kinds = {fact["kind"] for fact in catalog.values()}
    focuses = ["overview", "limitations"]
    if "numeric" in kinds:
        focuses.append("measurements")
    if "relationship" in kinds:
        focuses.append("dependencies")
    if _OUTSIDE_READ_ONLY.search(question):
        focuses = ["limitations"]
    return {
        "title": "ExplanationPlan",
        "type": "object",
        "properties": {
            "focus": {"type": "string", "enum": focuses},
            "fact_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(catalog)}
                if catalog
                else {"type": "string"},
                "minItems": 1 if catalog else 0,
                "maxItems": min(3, len(catalog)),
                "uniqueItems": True,
            },
        },
        "required": ["focus", "fact_ids"],
        "additionalProperties": False,
    }


def plan_input(question: str, template: Narrative) -> dict[str, Any]:
    return {
        "question": question,
        "server_status": template.status,
        "facts": fact_catalog(template),
        "output_schema": plan_schema(template, question),
        "instructions": (
            "Choose the focus relevant to the question and select up to three fact IDs in "
            "explanation order. For measurements select a numeric fact first; for dependencies "
            "select a relationship fact first. Use overview for general investigation questions. "
            "Use limitations for requests for certainty, remediation, or facts absent here. "
            "The application renders the chosen facts with exact values and citations. "
            "Never produce prose, numbers, commands, new facts or candidate rankings."
        ),
    }


def render_plan(plan: ExplanationPlan, template: Narrative, question: str = "") -> Narrative:
    """Independent semantic validation, then render only verified server-owned facts."""
    catalog = fact_catalog(template)
    if (
        len(set(plan.fact_ids)) != len(plan.fact_ids)
        or not set(plan.fact_ids) <= catalog.keys()
        or (bool(catalog) != bool(plan.fact_ids))
        or plan.focus not in plan_schema(template, question)["properties"]["focus"]["enum"]
    ):
        raise ValueError("plan references unavailable or duplicate facts")
    expected = {"measurements": "numeric", "dependencies": "relationship"}.get(plan.focus)
    if expected and catalog[plan.fact_ids[0]]["kind"] != expected:
        raise ValueError("plan focus does not match its first fact")
    if not catalog:
        return template.model_copy(deep=True)
    selected = [template.claims[int(identifier[1:])] for identifier in plan.fact_ids]
    sentences = [
        "The cited facts guide investigation; they do not establish a root cause."
        if plan.focus != "limitations"
        else "The evidence supports bounded observations, not certainty or corrective action."
    ]
    for claim in selected:
        if claim.claim_type == "numeric" and claim.metric is not None:
            labels = {
                "request_count": "request count",
                "error_count": "error count",
                "p50_ms": "median latency",
                "p95_ms": "ninety-fifth percentile latency",
            }
            metric = claim.metric
            if metric.metric_name in labels:
                sentences.append(
                    f"Recorded {labels[metric.metric_name]}: {metric.value} {metric.unit}; "
                    "the cited metric specifies the observation window."
                )
            else:
                sentences.append("The cited metric records the observation and its window.")
        elif claim.claim_type == "relationship":
            sentences.append("The cited directed call path connects the symptom to the candidate.")
        elif claim.claim_type == "hypothesis":
            sentences.append("The candidate warrants review of its recorded analysis measurements.")
        elif claim.claim_type == "limitation":
            sentences.append("The recorded evidence limitations apply to this investigation.")
        else:
            sentences.append("The cited observation supplies evidence for the selected cutoff.")
    citations = list(
        dict.fromkeys(identifier for claim in selected for identifier in claim.evidence_ids)
    )
    # Pydantic limits are enforced here, not bypassed with an unchecked dict update.
    summary = Summary(text=" ".join(dict.fromkeys(sentences)), evidence_ids=citations)
    return template.model_copy(update={"summary": summary}, deep=True)
