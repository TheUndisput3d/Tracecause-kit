"""Direct, source-verified producer/consumer parentage as typed MESSAGE_FLOW.

Async spans need not overlap. This rule accepts a consumer whose direct parent is
a producer in the same trace, on a different service, started at most one hour
earlier. It does not infer a broker, delivery guarantee or causal responsibility.
"""

from __future__ import annotations

import calendar
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from neo4j import Driver, Query
from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracecause.contracts.models import Contract, GraphEdge, NormalizedSpan, Sha256
from tracecause.graph.errors import GraphReadError
from tracecause.ingestion.normalize import normalized_span_semantic_hash
from tracecause.storage.artifacts import ArtifactRepository
from tracecause.storage.schema import SpanRow

HANDOFF_VERSION = "direct-producer-consumer-v1"
HANDOFF_NAMESPACE = UUID("64ed1738-a2fb-5c1c-9191-5ae1585c1eb2")
MAX_MESSAGE_SPANS = 4000
MAX_HANDOFFS = 2000
MAX_QUEUE_DELAY_NS = 3600_000_000_000


@dataclass(frozen=True)
class HandoffRecord:
    id: UUID
    producer: NormalizedSpan
    consumer: NormalizedSpan
    producer_artifact_hash: str
    consumer_artifact_hash: str
    producer_record_offset: int
    consumer_record_offset: int


class HandoffEvidence(Contract):
    evidence_id: str = Field(pattern=r"^handoff:[0-9a-f-]{36}$")
    episode_id: UUID
    snapshot_id: UUID
    as_of: AwareDatetime
    rule_version: Literal["direct-producer-consumer-v1"] = "direct-producer-consumer-v1"
    relationship: Literal["MESSAGE_FLOW"] = "MESSAGE_FLOW"
    producer: NormalizedSpan
    consumer: NormalizedSpan
    producer_artifact_hash: Sha256
    consumer_artifact_hash: Sha256
    producer_record_offset: int = Field(ge=0)
    consumer_record_offset: int = Field(ge=0)
    explanation: str


def _ns(value: datetime) -> int:
    return calendar.timegm(value.utctimetuple()) * 1_000_000_000 + value.microsecond * 1000


def _matches_source(row: SpanRow, span: NormalizedSpan) -> bool:
    fields = {
        "workspace_id": span.workspace_id,
        "episode_id": span.episode_id,
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "namespace": span.service_namespace,
        "environment": span.service_environment,
        "service_name": span.service_name,
        "service_key": span.service_key,
        "parent_span_id": span.parent_span_id,
        "operation": span.span_name,
        "start_ns": int(span.start_ns),
        "end_ns": int(span.end_ns),
        "kind": span.kind,
        "status": span.status,
        "safe_attributes": span.safe_attributes,
        "available_at": span.available_at,
        "quality_flags": span.quality_flags,
        "source_record_offset": span.source_record_offset,
        "source_line_number": span.source_line_number,
        "source_resource_index": span.source_resource_index,
        "source_scope_index": span.source_scope_index,
        "source_span_index": span.source_span_index,
        "normalized_hash": normalized_span_semantic_hash(span),
    }
    return all(getattr(row, key) == value for key, value in fields.items())


def derive_handoffs(
    spans: tuple[NormalizedSpan, ...],
    locators: dict[tuple[str, str], tuple[str, int]],
    workspace_id: UUID,
    episode_id: UUID,
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
) -> tuple[HandoffRecord, ...]:
    """Pure derivation; callers must first verify supplied spans and source locators."""
    if len(spans) > MAX_MESSAGE_SPANS:
        raise GraphReadError("message span limit exceeded")
    by_id: dict[tuple[str, str], NormalizedSpan] = {}
    for span in spans:
        if span.workspace_id != workspace_id or span.episode_id != episode_id:
            raise GraphReadError("message span is outside scope")
        key = (span.trace_id, span.span_id)
        if key in by_id:
            raise GraphReadError("duplicate message span identity")
        if int(span.end_ns) > as_of_ns:
            continue
        if mode == "available" and (span.available_at is None or _ns(span.available_at) > as_of_ns):
            continue
        by_id[key] = span
    records = []
    for consumer in sorted(by_id.values(), key=lambda s: (s.trace_id, s.span_id)):
        if consumer.kind != "CONSUMER" or consumer.parent_span_id is None:
            continue
        producer = by_id.get((consumer.trace_id, consumer.parent_span_id))
        if producer is None or producer.kind != "PRODUCER":
            continue
        if (
            not producer.service_key
            or not consumer.service_key
            or producer.service_key == consumer.service_key
            or int(consumer.start_ns) < int(producer.start_ns)
            or int(consumer.start_ns) - int(producer.start_ns) > MAX_QUEUE_DELAY_NS
        ):
            continue
        pkey, ckey = (producer.trace_id, producer.span_id), (consumer.trace_id, consumer.span_id)
        if pkey not in locators or ckey not in locators:
            raise GraphReadError("message source locator is unavailable")
        identifier = uuid5(
            HANDOFF_NAMESPACE,
            f"{HANDOFF_VERSION}:{workspace_id}:{episode_id}:{producer.trace_id}:"
            f"{producer.span_id}:{consumer.span_id}",
        )
        records.append(
            HandoffRecord(
                identifier,
                producer,
                consumer,
                locators[pkey][0],
                locators[ckey][0],
                locators[pkey][1],
                locators[ckey][1],
            )
        )
    if len(records) > MAX_HANDOFFS:
        raise GraphReadError("handoff limit exceeded")
    return tuple(records)


def load_handoffs(
    session: Session,
    artifacts: ArtifactRepository,
    workspace_id: UUID,
    episode_id: UUID,
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
) -> tuple[HandoffRecord, ...]:
    rows = session.scalars(
        select(SpanRow)
        .where(
            SpanRow.workspace_id == workspace_id,
            SpanRow.episode_id == episode_id,
            SpanRow.kind.in_(("PRODUCER", "CONSUMER")),
        )
        .limit(MAX_MESSAGE_SPANS + 1)
    ).all()
    if len(rows) > MAX_MESSAGE_SPANS:
        raise GraphReadError("message span limit exceeded")
    wanted = {(row.trace_id, row.span_id): row for row in rows}
    verified: dict[tuple[str, str], NormalizedSpan] = {}
    locators: dict[tuple[str, str], tuple[str, int]] = {}
    artifact_hashes = {row.source_artifact_hash for row in rows}
    if len(artifact_hashes) > 20:
        raise GraphReadError("message artifact limit exceeded")
    for artifact_hash in sorted(artifact_hashes):
        offset = 0
        for line in artifacts.iter_lines(artifact_hash):
            span = NormalizedSpan.model_validate_json(line)
            key = (span.trace_id, span.span_id)
            row = wanted.get(key)
            if row is not None and row.source_artifact_hash == artifact_hash:
                if key in verified or not _matches_source(row, span):
                    raise GraphReadError("message span differs from immutable source")
                verified[key] = span
                locators[key] = (artifact_hash, offset)
            offset += len(line)
        # iter_lines verifies the complete hash before any derived data is returned.
    if verified.keys() != wanted.keys():
        raise GraphReadError("message span source is unavailable")
    return derive_handoffs(
        tuple(verified.values()), locators, workspace_id, episode_id, as_of_ns, mode
    )


def _node(snapshot_id: UUID, label: str, source: str) -> str:
    return hashlib.sha256(f"{snapshot_id}:{label}:{source}".encode()).hexdigest()


def _row(record: HandoffRecord) -> dict[str, Any]:
    p, c = record.producer, record.consumer
    availability = (
        max(p.available_at, c.available_at) if p.available_at and c.available_at else None
    )
    return dict(
        source_id=str(record.id),
        producer_service=p.service_key,
        consumer_service=c.service_key,
        trace_id=p.trace_id,
        producer_span_id=p.span_id,
        consumer_span_id=c.span_id,
        start_ns=int(p.start_ns),
        end_ns=max(int(p.end_ns), int(c.end_ns)),
        available_at_ns=_ns(availability) if availability else None,
        producer_hash=record.producer_artifact_hash,
        consumer_hash=record.consumer_artifact_hash,
        rule_version=HANDOFF_VERSION,
    )


def project_handoffs(
    driver: Driver,
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
    records: tuple[HandoffRecord, ...],
) -> None:
    if not records:
        return
    rows = [
        dict(
            **_row(r),
            node_id=_node(snapshot_id, "HandoffObservation", str(r.id)),
            producer_node=_node(snapshot_id, "Service", str(r.producer.service_key)),
            consumer_node=_node(snapshot_id, "Service", str(r.consumer.service_key)),
        )
        for r in records
    ]
    with driver.session(database="neo4j") as graph:
        result = graph.run(
            Query(
                "UNWIND $rows AS row MATCH (p:Service {id:row.producer_node,"
                "workspace_id:$wid,episode_id:$eid,snapshot_id:$sid}) "
                "MATCH (c:Service {id:row.consumer_node,workspace_id:$wid,"
                "episode_id:$eid,snapshot_id:$sid}) "
                "MERGE (h:HandoffObservation {id:row.node_id,workspace_id:$wid,"
                "episode_id:$eid,snapshot_id:$sid}) SET h += row "
                "MERGE (h)-[:FROM_SERVICE {handoff:true,workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(p) "
                "MERGE (h)-[:TO_SERVICE {handoff:true,workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}]->(c) "
                "MERGE (p)-[:MESSAGE_FLOW {handoff:true,workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid,evidence_id:row.source_id}]->(c) "
                "RETURN count(h) AS count",
                timeout=10.0,
            ),
            rows=rows,
            wid=str(workspace_id),
            eid=str(episode_id),
            sid=str(snapshot_id),
        ).single()
        if result is None or result["count"] != len(records):
            raise GraphReadError("handoff projection has missing service endpoints")


def verify_handoffs(
    driver: Driver,
    workspace_id: UUID,
    episode_id: UUID,
    snapshot_id: UUID,
    records: tuple[HandoffRecord, ...],
    as_of_ns: int,
    mode: Literal["event", "available"] = "event",
    *,
    full_projection: bool = False,
) -> None:
    with driver.session(database="neo4j") as graph:
        all_nodes = graph.run(
            Query(
                "MATCH (h:HandoffObservation {workspace_id:$wid,episode_id:$eid,"
                "snapshot_id:$sid}) RETURN properties(h) AS props LIMIT $limit",
                timeout=5.0,
            ),
            wid=str(workspace_id),
            eid=str(episode_id),
            sid=str(snapshot_id),
            limit=MAX_HANDOFFS + 1,
        ).data()
        all_links = graph.run(
            Query(
                "MATCH (a)-[r]->(b) WHERE "
                "(r.handoff=true OR type(r)='MESSAGE_FLOW' OR a:HandoffObservation "
                "OR b:HandoffObservation) AND "
                "(a.snapshot_id=$sid OR b.snapshot_id=$sid OR r.snapshot_id=$sid) "
                "RETURN a.id AS source,b.id AS target,type(r) AS kind,properties(r) AS props,"
                "a.workspace_id AS aw,a.episode_id AS ae,a.snapshot_id AS ass,"
                "b.workspace_id AS bw,b.episode_id AS be,b.snapshot_id AS bss,"
                "a.service_key AS ak,b.service_key AS bk,"
                "labels(a) AS al,labels(b) AS bl LIMIT $limit",
                timeout=5.0,
            ),
            sid=str(snapshot_id),
            limit=MAX_HANDOFFS * 3 + 1,
        ).data()
        if len(all_nodes) > MAX_HANDOFFS or len(all_links) > MAX_HANDOFFS * 3:
            raise GraphReadError("handoff projection limit exceeded")
        if full_projection and {node["props"].get("source_id") for node in all_nodes} != {
            str(record.id) for record in records
        }:
            raise GraphReadError("complete handoff projection membership differs from source")
        expected_links: Counter[tuple[str, str, str, str | None]] = Counter()
        service_keys: dict[str, str] = {}
        seen_all = set()
        for node in all_nodes:
            props = node["props"]
            identifier = props.get("source_id")
            try:
                UUID(identifier)
            except (ValueError, TypeError, AttributeError) as exc:
                raise GraphReadError("handoff projection identity is invalid") from exc
            own = _node(snapshot_id, "HandoffObservation", identifier)
            producer = _node(snapshot_id, "Service", str(props.get("producer_service")))
            consumer = _node(snapshot_id, "Service", str(props.get("consumer_service")))
            service_keys[producer] = str(props.get("producer_service"))
            service_keys[consumer] = str(props.get("consumer_service"))
            if identifier in seen_all or props.get("id") != own:
                raise GraphReadError("handoff projection identity is invalid")
            seen_all.add(identifier)
            expected_links.update(
                [
                    ("FROM_SERVICE", own, producer, None),
                    ("TO_SERVICE", own, consumer, None),
                    ("MESSAGE_FLOW", producer, consumer, identifier),
                ]
            )
        actual_links: Counter[tuple[str, str, str, str | None]] = Counter()
        for link in all_links:
            properties = link["props"]
            expected_props = dict(
                handoff=True,
                workspace_id=str(workspace_id),
                episode_id=str(episode_id),
                snapshot_id=str(snapshot_id),
            )
            if link["kind"] == "MESSAGE_FLOW":
                expected_props["evidence_id"] = properties.get("evidence_id")
            if properties != expected_props or any(
                link[key] != value
                for key, value in (
                    ("aw", str(workspace_id)),
                    ("bw", str(workspace_id)),
                    ("ae", str(episode_id)),
                    ("be", str(episode_id)),
                    ("ass", str(snapshot_id)),
                    ("bss", str(snapshot_id)),
                )
            ):
                raise GraphReadError("handoff relationship properties or scope differ")
            for endpoint, key, labels in (
                (link["source"], link["ak"], link["al"]),
                (link["target"], link["bk"], link["bl"]),
            ):
                if endpoint in service_keys and (
                    key != service_keys[endpoint] or "Service" not in labels
                ):
                    raise GraphReadError("handoff endpoint service differs from source")
            actual_links[
                (link["kind"], link["source"], link["target"], properties.get("evidence_id"))
            ] += 1
        if actual_links != expected_links:
            raise GraphReadError("handoff relationship membership differs from source")
    # The full Counter audit above already proves exactly one typed, scoped pair
    # of service links and one MESSAGE_FLOW per observation, including endpoint
    # keys and labels. Joining each observation back to every parallel message
    # edge repeats that proof quadratically for busy producer/consumer pairs.
    rows = []
    for node in all_nodes:
        props = node["props"]
        end_ns, available_ns = props.get("end_ns"), props.get("available_at_ns")
        if type(end_ns) is not int or (available_ns is not None and type(available_ns) is not int):
            raise GraphReadError("handoff projection timestamp differs from source")
        if end_ns <= as_of_ns and (
            mode == "event" or (available_ns is not None and available_ns <= as_of_ns)
        ):
            rows.append(props)
    expected = {str(r.id): _row(r) for r in records}
    if len(rows) != len(expected):
        raise GraphReadError("handoff projection membership differs from source")
    seen = set()
    for props in rows:
        identifier = props.get("source_id")
        item = expected.get(identifier)
        if identifier in seen or item is None or any(props.get(k) != v for k, v in item.items()):
            raise GraphReadError("handoff projection differs from source")
        seen.add(identifier)


def handoff_edges(records: tuple[HandoffRecord, ...]) -> tuple[GraphEdge, ...]:
    grouped: dict[tuple[str, str], list[HandoffRecord]] = defaultdict(list)
    for record in records:
        assert record.producer.service_key is not None and record.consumer.service_key is not None
        grouped[(record.producer.service_key, record.consumer.service_key)].append(record)
    return tuple(
        GraphEdge(
            edge_id="handoff-edge:" + hashlib.sha256((source + target).encode()).hexdigest(),
            source_service_id=source,
            target_service_id=target,
            relationship="MESSAGE_FLOW",
            supporting_evidence_ids=[
                f"handoff:{r.id}"
                for r in sorted(group, key=lambda r: (-int(r.consumer.end_ns), str(r.id)))[:20]
            ],
        )
        for (source, target), group in sorted(grouped.items())
    )


def handoff_evidence(record: HandoffRecord, snapshot_id: UUID, as_of: datetime) -> HandoffEvidence:
    if max(int(record.producer.end_ns), int(record.consumer.end_ns)) > _ns(as_of):
        raise GraphReadError("handoff evidence is after the requested cutoff")
    return HandoffEvidence(
        evidence_id=f"handoff:{record.id}",
        episode_id=record.producer.episode_id,
        snapshot_id=snapshot_id,
        as_of=as_of,
        producer=record.producer,
        consumer=record.consumer,
        producer_artifact_hash=record.producer_artifact_hash,
        consumer_artifact_hash=record.consumer_artifact_hash,
        producer_record_offset=record.producer_record_offset,
        consumer_record_offset=record.consumer_record_offset,
        explanation="The consumer directly references this producer as its parent in the "
        "same trace. Both exact normalized spans were verified against immutable artifacts "
        "and the scoped snapshot projection. This records an observed asynchronous handoff, "
        "not a synchronous call or proof of cause.",
    )
