"""Composed contracts for operational graph and historical investigation views."""

from uuid import UUID

from pydantic import AwareDatetime, Field

from tracecause.contracts.models import Contract, GraphResponse, TemporalMode
from tracecause.operational.models import OperationalEdge, OperationalFact, OperationalNode
from tracecause.operational.reasoning_models import (
    ChangeReasoningResponse,
    HistoricalReasoningResponse,
)


class OperationsResponse(Contract):
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    temporal_mode: TemporalMode
    application_id: str | None
    application_name: str | None
    topology: str | None
    graph: GraphResponse
    nodes: list[OperationalNode] = Field(max_length=2500)
    edges: list[OperationalEdge] = Field(max_length=5000)
    facts: list[OperationalFact] = Field(max_length=500)
    change_reasoning: ChangeReasoningResponse | None
    history: HistoricalReasoningResponse
    limitations: list[str] = Field(max_length=8)
