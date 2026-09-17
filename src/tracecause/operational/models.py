"""Strict operational source records; no evaluation controls or inferred causes."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)

from tracecause.contracts.models import Contract, Sha256
from tracecause.ingestion.identity import AliasRegistry
from tracecause.ingestion.redaction import SENSITIVE

IdentityText = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]
Scalar = (
    Annotated[str, StringConstraints(max_length=1000)]
    | StrictBool
    | StrictInt
    | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    | None
)


class ServiceRef(Contract):
    namespace: IdentityText
    environment: IdentityText
    name: IdentityText

    def key(self, workspace_id: UUID) -> str:
        result = AliasRegistry().resolve(workspace_id, self.namespace, self.environment, self.name)
        if result.key is None or (result.namespace, result.environment, result.name) != (
            self.namespace,
            self.environment,
            self.name,
        ):
            raise ValueError("operational service identity is invalid")
        return result.key


class OperationalEvent(Contract):
    id: UUID
    kind: Literal["deployment", "configuration", "runtime", "ownership", "incident_note"]
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    service: ServiceRef
    source: str = Field(min_length=1, max_length=200)
    details: dict[str, Scalar] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def typed_details(self) -> "OperationalEvent":
        required = {
            "deployment": {"version", "revision", "artifact_sha256", "runtime_id"},
            "configuration": {"key", "previous_value", "value", "runtime_id"},
            "runtime": {"runtime_id", "host", "process_id", "runtime_type", "version"},
            "ownership": {"team"},
            "incident_note": {"title", "summary"},
        }[self.kind]
        optional = {"previous_version", "image_digest", "region", "zone", "platform"}
        if not required <= self.details.keys() or self.details.keys() - required - optional:
            raise ValueError("operational event details do not match its kind")
        if self.available_at < self.occurred_at:
            raise ValueError("operational availability predates the event")
        if any(SENSITIVE.search(key) for key in self.details):
            raise ValueError("sensitive operational field is forbidden")
        for key in required - {"previous_value", "value", "process_id"}:
            if not isinstance(self.details[key], str) or not self.details[key]:
                raise ValueError("operational identity values must be nonempty strings")
        if self.kind == "deployment":
            value = str(self.details["artifact_sha256"])
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("deployment artifact SHA-256 is invalid")
        if self.kind == "configuration":
            key = str(self.details["key"])
            if len(key) > 200 or SENSITIVE.search(key):
                raise ValueError("configuration key is sensitive or exceeds the field limit")
        if self.kind == "runtime":
            process_id = self.details["process_id"]
            if type(process_id) is not int or not 1 <= process_id <= 2**31 - 1:
                raise ValueError("runtime process ID must be a positive integer")
            if self.details["runtime_type"] not in {"process", "container", "kubernetes_pod"}:
                raise ValueError("runtime type is unsupported")
        return self


class OperationalBundle(Contract):
    schema_version: Literal["1.0"]
    application_id: IdentityText
    application_name: str = Field(min_length=1, max_length=120)
    topology: str = Field(min_length=1, max_length=200)
    events: list[OperationalEvent] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_events(self) -> "OperationalBundle":
        if len({event.id for event in self.events}) != len(self.events):
            raise ValueError("operational event IDs must be unique")
        if len({event.service.environment for event in self.events}) != 1:
            raise ValueError("an operational bundle must describe one environment")
        return self


class OperationalFact(Contract):
    event: OperationalEvent
    service_key: Sha256
    evidence_id: str = Field(pattern=r"^operation:[0-9a-f-]{36}$")
    artifact_hash: Sha256
    application_id: str
    episode_id: UUID
    snapshot_id: UUID


class OperationalNode(Contract):
    id: str
    kind: Literal[
        "application",
        "service",
        "deployment",
        "version",
        "configuration",
        "runtime",
        "host",
        "team",
        "incident",
    ]
    label: str
    evidence_ids: list[str] = Field(max_length=100)


class OperationalEdge(Contract):
    source: str
    target: str
    relationship: Literal[
        "HAS_SERVICE",
        "INSTANCE_OF",
        "DEPLOYS",
        "VERSION_OF",
        "RUNS_ON",
        "HOSTED_ON",
        "CONFIGURES",
        "OWNED_BY",
        "RECORDED_ON",
        "PREVIOUS_INCIDENT",
    ]
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class OperationalEvidence(Contract):
    evidence_id: str
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    artifact_hash: Sha256
    application_id: str
    event: OperationalEvent
    source_json: str = Field(max_length=20000)
