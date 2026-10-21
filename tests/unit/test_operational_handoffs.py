"""Async relation derivation requires exact, bounded producer/consumer lineage."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracecause.contracts.models import NormalizedSpan
from tracecause.graph.errors import GraphReadError
from tracecause.operational.handoffs import derive_handoffs, handoff_edges, handoff_evidence

TIME = datetime(2026, 1, 1, tzinfo=UTC)
BASE_NS = 1_767_225_600_000_000_000


def source_pair():
    workspace, episode = uuid4(), uuid4()
    common = dict(
        workspace_id=workspace,
        episode_id=episode,
        trace_id="a" * 32,
        span_name="message",
        status="OK",
        available_at=TIME + timedelta(seconds=5),
        safe_attributes={},
        source_hash="b" * 64,
        source_record_offset=0,
        quality_flags=[],
    )
    producer = NormalizedSpan(
        **common,
        span_id="c" * 16,
        parent_span_id=None,
        service_key="1" * 64,
        kind="PRODUCER",
        start_ns=str(BASE_NS),
        end_ns=str(BASE_NS + 1_000_000_000),
    )
    consumer = NormalizedSpan(
        **common,
        span_id="d" * 16,
        parent_span_id=producer.span_id,
        service_key="2" * 64,
        kind="CONSUMER",
        start_ns=str(BASE_NS + 2_000_000_000),
        end_ns=str(BASE_NS + 3_000_000_000),
    )
    return producer, consumer


def derive(producer, consumer, *, mode="event", cutoff=BASE_NS + 10_000_000_000):
    locators = {
        (s.trace_id, s.span_id): ("e" * 64, i * 100) for i, s in enumerate((producer, consumer))
    }
    return derive_handoffs(
        (producer, consumer), locators, producer.workspace_id, producer.episode_id, cutoff, mode
    )


def test_async_nonoverlap_is_explicit_message_flow_and_inspectable():
    producer, consumer = source_pair()
    records = derive(producer, consumer)
    assert len(records) == 1
    assert int(producer.end_ns) < int(consumer.start_ns)
    edge = handoff_edges(records)[0]
    assert edge.relationship == "MESSAGE_FLOW"
    assert edge.source_service_id == producer.service_key
    assert edge.target_service_id == consumer.service_key
    evidence = handoff_evidence(records[0], uuid4(), TIME + timedelta(seconds=10))
    assert evidence.producer.span_id == evidence.consumer.parent_span_id
    assert evidence.producer.trace_id == evidence.consumer.trace_id
    assert evidence.evidence_id == edge.supporting_evidence_ids[0]
    assert evidence.consumer_record_offset == 100


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "SERVER"},
        {"parent_span_id": "f" * 16},
        {"trace_id": "f" * 32},
        {"service_key": "1" * 64},
        {"start_ns": str(BASE_NS - 1)},
        {"start_ns": str(BASE_NS + 3601_000_000_000), "end_ns": str(BASE_NS + 3602_000_000_000)},
    ],
)
def test_missing_wrong_direction_and_excessive_queue_delay_not_inferred(updates):
    producer, consumer = source_pair()
    assert not derive(
        producer, consumer.model_copy(update=updates), cutoff=BASE_NS + 4000_000_000_000
    )


def test_future_and_unavailable_parent_or_child_excluded():
    producer, consumer = source_pair()
    assert not derive(producer, consumer, cutoff=BASE_NS + 2_500_000_000)
    assert not derive(producer, consumer, mode="available", cutoff=BASE_NS + 4_000_000_000)
    assert len(derive(producer, consumer, mode="event", cutoff=BASE_NS + 4_000_000_000)) == 1
    assert not derive(
        producer.model_copy(update={"available_at": None}), consumer, mode="available"
    )


def test_cross_scope_duplicate_missing_locator_fail_closed():
    producer, consumer = source_pair()
    with pytest.raises(GraphReadError, match="scope"):
        derive(producer, consumer.model_copy(update={"episode_id": uuid4()}))
    with pytest.raises(GraphReadError, match="duplicate"):
        derive(producer, producer)
    with pytest.raises(GraphReadError, match="locator"):
        derive_handoffs(
            (producer, consumer),
            {},
            producer.workspace_id,
            producer.episode_id,
            BASE_NS + 10_000_000_000,
        )
