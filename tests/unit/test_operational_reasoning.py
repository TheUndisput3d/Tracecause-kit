"""Operational joins: direction, temporal ambiguity, honest historical evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from tracecause.contracts.models import GraphResponse, TemporalMode
from tracecause.operational.models import OperationalEvent, OperationalFact, ServiceRef
from tracecause.operational.reasoning import (
    EpisodeProfile,
    compare_history,
    degradation_onsets,
    reason_changes,
)
from tracecause.telemetry.signals import ComputedObservation, SignalFeature

WORKSPACE = UUID("00000000-0000-0000-0000-000000000001")
END = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def key(name):
    return ServiceRef(namespace="store", environment="test", name=name).key(WORKSPACE)


def graph(pairs=(("a", "b"),), *, end=END, mode=TemporalMode.EVENT, names="abcd"):
    return GraphResponse(
        episode_id=uuid4(),
        snapshot_id=uuid4(),
        as_of=end,
        temporal_mode=mode,
        nodes=[
            dict(service_id=key(n), service_key=key(n), display_name=n, quality_flags=[])
            for n in names
        ],
        edges=[
            dict(
                edge_id=a + b,
                source_service_id=key(a),
                target_service_id=key(b),
                relationship="CALLS",
                supporting_evidence_ids=["observation:" + str(uuid4())],
            )
            for a, b in pairs
        ],
        truncated=False,
        warnings=[],
    )


def fact(g, name="b", seconds=-30, *, kind="deployment", value="2.0", available=None):
    details = dict(
        version=value, revision="commit-v2", artifact_sha256="a" * 64, runtime_id="runtime-b"
    )
    if kind == "configuration":
        details = dict(key="timeout_ms", previous_value=100, value=value, runtime_id="runtime-b")
    event = OperationalEvent(
        id=uuid4(),
        kind=kind,
        occurred_at=g.as_of + timedelta(seconds=seconds),
        available_at=available or g.as_of + timedelta(seconds=seconds),
        service=ServiceRef(namespace="store", environment="test", name=name),
        details=details,
        source="unit-controlled-lifecycle-log",
    )
    return OperationalFact(
        event=event,
        service_key=key(name),
        evidence_id=f"operation:{event.id}",
        artifact_hash="b" * 64,
        application_id="store",
        episode_id=g.episode_id,
        snapshot_id=g.snapshot_id,
    )


def signal(g, name="b", *, onset=-20, latency=5.0, errors=0.0, volume=0.0):
    end_ns = int(g.as_of.timestamp()) * 1_000_000_000
    return SignalFeature(
        service_key=key(name),
        analysis_observation_id=uuid5(NAMESPACE_URL, str(g.episode_id) + name),
        baseline_observation_ids=(uuid4(),),
        analysis_bucket_ids=(),
        baseline_start_ns=end_ns - 360_000_000_000,
        baseline_end_ns=end_ns - 60_000_000_000,
        analysis_start_ns=end_ns - 60_000_000_000,
        analysis_end_ns=end_ns,
        latency_deviation_z=latency,
        error_excess_z=errors,
        volume_deviation_z=volume,
        first_anomalous_bucket_ns=None if onset is None else end_ns + onset * 1_000_000_000,
        completeness=1.0,
        baseline_bucket_count=30,
        baseline_sample_count=100,
        flags=(),
        formulas={},
        text="controlled unit signal",
    )


def profile(
    g, *, facts=(), signals=(), application="store", environment="test", workspace=WORKSPACE
):
    return EpisodeProfile(
        workspace_id=workspace,
        application_id=application,
        environment=environment,
        display_name="Controlled episode",
        started_at=g.as_of - timedelta(minutes=10),
        ended_at=g.as_of,
        available_at=g.as_of,
        graph=g,
        facts=tuple(facts),
        signals=tuple(signals),
    )


def history(current, previous):
    return compare_history(
        current, previous, as_of=current.graph.as_of, temporal_mode=current.graph.temporal_mode
    )


def test_prior_downstream_change_has_complete_evidence_path():
    g = graph((("a", "b"), ("b", "c")))
    f, s = fact(g, "c"), signal(g, "c")
    result = reason_changes(g, (f,), (s,), key("a"))
    change = result.changes[0]
    assert change.association == "precedes_observed_degradation"
    assert change.priority == "review_first"
    assert change.service_ids == [key("a"), key("b"), key("c")]
    assert len(change.call_edges) == 2
    assert change.evidence_id == f.evidence_id
    assert change.signal.observation_evidence_id == f"observation:{s.analysis_observation_id}"
    assert "do not prove causation" in change.explanation


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-21, "precedes_observed_degradation"),
        (-20, "overlaps_onset_bucket"),
        (-15, "overlaps_onset_bucket"),
        (-10, "after_observed_degradation"),
        (-1, "after_observed_degradation"),
    ],
)
def test_change_onset_bucket_boundaries(seconds, expected):
    g = graph()
    result = reason_changes(g, (fact(g, seconds=seconds),), (signal(g),), key("a"))
    assert result.changes[0].association == expected


@pytest.mark.parametrize(
    "pairs", [(("b", "a"),), (("c", "d"),), (("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"))]
)
def test_reverse_disconnected_and_hop_limit_are_not_proof(pairs):
    g = graph(pairs, names="abcde")
    target = "e" if len(pairs) == 4 else "b"
    result = reason_changes(g, (fact(g, target),), (signal(g, target),), key("a"))
    assert result.changes[0].association == "outside_dependency_path"
    assert result.changes[0].service_ids == []
    assert result.changes[0].call_edges == []


def test_cycle_is_bounded_and_zero_hop_selected_service_is_supported():
    g = graph((("a", "b"), ("b", "a")))
    result = reason_changes(g, (fact(g, "a"),), (signal(g, "a"),), key("a"))
    assert result.changes[0].service_ids == [key("a")]
    assert result.changes[0].priority == "review_first"


@pytest.mark.parametrize(
    ("latency", "onset", "expected"),
    [(0.0, -20, "reachable_without_detected_degradation"), (5.0, None, "unknown_onset")],
)
def test_healthy_or_missing_onset_cannot_receive_review_priority(latency, onset, expected):
    g = graph()
    result = reason_changes(g, (fact(g),), (signal(g, latency=latency, onset=onset),), key("a"))
    assert result.changes[0].association == expected
    assert result.changes[0].priority == "context_only"


def test_future_unavailable_and_old_events_excluded():
    g = graph(mode=TemporalMode.AVAILABLE)
    result = reason_changes(
        g,
        (
            fact(g, seconds=1),
            fact(g, available=g.as_of + timedelta(seconds=1)),
            fact(g, seconds=-86401),
        ),
        (signal(g),),
        key("a"),
    )
    assert not result.changes
    event_graph = g.model_copy(update={"temporal_mode": TemporalMode.EVENT})
    assert (
        len(
            reason_changes(
                event_graph,
                (fact(g, available=g.as_of + timedelta(seconds=1)),),
                (signal(g),),
                key("a"),
            ).changes
        )
        == 1
    )


def test_future_signals_not_used_and_latest_eligible_signal_wins():
    g = graph()
    first = signal(g, latency=8)
    old = replace(first, analysis_end_ns=first.analysis_end_ns - 10_000_000_000)
    now = replace(first, latency_deviation_z=0)
    future = replace(first, analysis_end_ns=first.analysis_end_ns + 10_000_000_000)
    result = reason_changes(g, (fact(g),), (future, old, now), key("a"))
    assert result.changes[0].priority == "context_only"


def test_scope_and_identity_tampering_fail_closed():
    g = graph()
    f = fact(g)
    for bad in (
        f.model_copy(update={"episode_id": uuid4()}),
        f.model_copy(update={"snapshot_id": uuid4()}),
        f.model_copy(update={"evidence_id": "operation:" + str(uuid4())}),
    ):
        with pytest.raises(ValueError):
            reason_changes(g, (bad,), (), key("a"))
    with pytest.raises(ValueError, match="duplicated"):
        reason_changes(g, (f, f), (), key("a"))
    with pytest.raises(ValueError, match="not visible"):
        reason_changes(g, (), (), key("unknown"))


def test_rank_includes_conflicting_changes_and_honest_bound():
    g = graph()
    priority = fact(g, seconds=-30)
    later = fact(g, seconds=-5)
    disconnected = fact(g, "d", seconds=-1)
    changes = reason_changes(
        g, (later, disconnected, priority), (signal(g), signal(g, "d")), key("a")
    ).changes
    assert changes[0].event_id == priority.event.id
    assert {c.association for c in changes} == {
        "precedes_observed_degradation",
        "after_observed_degradation",
        "outside_dependency_path",
    }
    result = reason_changes(
        g, tuple(fact(g, seconds=-30 - i) for i in range(30)), (signal(g),), key("a")
    )
    assert len(result.changes) == 24
    assert result.omitted_changes == 6


def test_history_uses_typed_signal_kinds_and_direction():
    current_graph = graph()
    earlier_graph = graph(end=END - timedelta(days=1))
    other_graph = graph((("b", "a"),), end=END - timedelta(days=2))
    current = profile(current_graph, signals=[signal(current_graph)])
    same = profile(earlier_graph, signals=[signal(earlier_graph)])
    different = profile(other_graph, signals=[signal(other_graph, latency=0, errors=5)])
    result = history(current, [different, same])
    assert result.matches[0].episode_id == same.graph.episode_id
    assert result.matches[0].similarity == 1
    assert result.matches[1].similarity == pytest.approx(0.2)
    assert result.matches[1].dependency_similarity == 0
    assert result.matches[1].signal_similarity == 0
    proof = result.matches[0].matching_signal_evidence[0]
    assert proof.snapshot_id == same.graph.snapshot_id
    assert proof.as_of == earlier_graph.as_of
    assert result.matches[1].previous_only_dependencies[0].source_service_id == key("b")


def test_history_excludes_future_overlap_cross_scope_and_unavailable():
    g = graph(mode=TemporalMode.AVAILABLE)
    old = graph(end=END - timedelta(days=1), mode=TemporalMode.AVAILABLE)
    current = profile(g)
    prior = profile(old)
    invalid = [
        replace(prior, workspace_id=uuid4()),
        replace(prior, application_id="other"),
        replace(prior, environment="production"),
        replace(prior, ended_at=END + timedelta(seconds=1)),
        replace(prior, ended_at=current.started_at + timedelta(seconds=1)),
        replace(prior, available_at=END + timedelta(seconds=1)),
        replace(prior, available_at=None),
        current,
    ]
    for candidate in invalid:
        assert not history(current, [candidate]).matches
    assert history(current, [prior]).matches


def test_history_state_deltas_keep_sources_and_unknown_distinct():
    g, old = graph(), graph(end=END - timedelta(days=1))
    current_fact, previous_fact = fact(g, value="2.0"), fact(old, value="1.0")
    configuration = fact(g, "a", kind="configuration", value=None)
    current = profile(g, facts=[current_fact, configuration])
    prior = profile(old, facts=[previous_fact])
    match = history(current, [prior]).matches[0]
    version = next(d for d in match.state_differences if d.field == "deployment.version")
    assert (version.previous_value, version.current_value, version.status) == (
        "1.0",
        "2.0",
        "changed",
    )
    assert version.previous_evidence.evidence_id == previous_fact.evidence_id
    assert version.current_evidence.evidence_id == current_fact.evidence_id
    config = next(d for d in match.state_differences if d.field.startswith("configuration."))
    assert config.status == "previous_unknown"
    assert config.current_evidence and not config.previous_evidence
    assert config.current_value is None


def test_history_configuration_types_not_coerced_and_future_state_excluded():
    g, old = graph(), graph(end=END - timedelta(days=1))
    current = profile(
        g, facts=[fact(g, kind="configuration", value=True), fact(g, seconds=1, value="future")]
    )
    prior = profile(old, facts=[fact(old, kind="configuration", value=1)])
    differences = history(current, [prior]).matches[0].state_differences
    assert len(differences) == 1
    assert differences[0].status == "changed"
    assert type(differences[0].current_value) is bool
    assert type(differences[0].previous_value) is int


def test_empty_components_do_not_invent_full_signal_overlap():
    current = profile(graph(()))
    prior = profile(graph((), end=END - timedelta(days=1)))
    match = history(current, [prior]).matches[0]
    assert match.service_similarity == 1
    assert match.dependency_similarity is None
    assert match.signal_similarity is None
    assert match.similarity == 1  # Only the explicitly displayed service component is present.


def test_history_caps_and_duplicate_profiles_fail_closed():
    current = profile(graph())
    previous = [profile(graph(end=END - timedelta(days=i + 1))) for i in range(8)]
    result = history(current, previous)
    assert len(result.matches) == 6
    assert result.eligible_episodes == 8
    assert result.omitted_matches == 2
    with pytest.raises(ValueError, match="more than once"):
        history(current, [previous[0], previous[0]])
    with pytest.raises(ValueError, match="limit"):
        history(current, previous * 5)


def test_volume_alone_is_context_and_missing_baseline_blocks_degradation():
    g = graph()
    volume_only = signal(g, latency=0, volume=10)
    result = reason_changes(g, (fact(g),), (volume_only,), key("a"))
    assert result.changes[0].signal.anomalous
    assert not result.changes[0].signal.degradation_detected
    assert result.changes[0].priority == "context_only"
    missing = replace(signal(g), baseline_bucket_count=5)
    assert reason_changes(g, (fact(g),), (missing,), key("a")).changes[0].priority == "context_only"
    missing = replace(signal(g), baseline_sample_count=9)
    assert reason_changes(g, (fact(g),), (missing,), key("a")).changes[0].priority == "context_only"


def test_latency_error_onset_ignores_volume_and_keeps_source_bucket_time():
    g = graph()
    s = signal(g, volume=10, onset=-50)
    end = s.analysis_end_ns

    def observation(start, count=20, errors=0, latency=10.0):
        return ComputedObservation(
            id=uuid4(),
            workspace_id=WORKSPACE,
            episode_id=g.episode_id,
            rule_version="controlled-unit",
            observation_type="service",
            service_key=key("b"),
            caller_service_key=None,
            callee_service_key=None,
            start_ns=end + start * 1_000_000_000,
            end_ns=end + (start + 10) * 1_000_000_000,
            sample_count=count,
            error_count=errors,
            p50_ms=latency,
            p95_ms=latency,
            attributes={},
            provenance_hash="a" * 64,
            supports=(),
            available_at=None,
        )

    baseline = [observation(-120 + i * 10) for i in range(6)]
    volume = observation(-50, count=500)
    failure = observation(-20, errors=2)
    future = observation(0, errors=15)
    s = replace(
        s,
        baseline_observation_ids=tuple(row.id for row in baseline),
        analysis_bucket_ids=(volume.id, failure.id, future.id),
    )
    onsets = degradation_onsets((s,), (*baseline, volume, failure, future))
    assert onsets[key("b")] == end - 20_000_000_000
    uncertain = reason_changes(g, (fact(g),), (s,), key("a"))
    assert uncertain.changes[0].association == "unknown_onset"
    result = reason_changes(g, (fact(g),), (s,), key("a"), degradation_onset_ns=onsets)
    assert result.changes[0].priority == "review_first"
    assert result.changes[0].signal.first_degradation_bucket_ns == str(end - 20_000_000_000)
    assert degradation_onsets((s,), baseline[:5]) == {key("b"): None}
    wrong = replace(baseline[0], service_key=key("c"))
    with pytest.raises(ValueError, match="another service"):
        degradation_onsets((s,), (wrong, *baseline[1:]))


def test_downstream_frontier_orders_review_and_exposes_exact_counterevidence():
    g = graph((("a", "b"), ("b", "c")))
    upstream = fact(g, "a", seconds=-25)
    downstream = fact(g, "c", seconds=-30)
    s = (signal(g, "a"), signal(g, "b"), signal(g, "c"))
    result = reason_changes(g, (upstream, downstream), s, key("a"))
    assert result.changes[0].event_id == downstream.event.id
    assert result.changes[0].downstream_degradation_count == 0
    upstream_result = result.changes[1]
    assert upstream_result.priority == "review_first"
    assert upstream_result.downstream_degradation_count == 2
    proof = next(p for p in upstream_result.downstream_degradations if p.service_id == key("c"))
    assert proof.service_ids == [key("a"), key("b"), key("c")]
    assert len(proof.edges) == 2
    assert proof.signal.observation_evidence_id == f"observation:{s[2].analysis_observation_id}"
    healthy = (s[0], replace(s[1], latency_deviation_z=0), replace(s[2], latency_deviation_z=0))
    result = reason_changes(g, (upstream, downstream), healthy, key("a"))
    assert result.changes[0].event_id == upstream.event.id


def test_async_message_flow_is_reasoned_without_relabelling_as_call():
    g = graph((("a", "b"), ("b", "c")))
    g.edges[0].relationship = "MESSAGE_FLOW"
    result = reason_changes(g, (fact(g, "c"),), (signal(g, "c"),), key("a"))
    assert result.changes[0].priority == "review_first"
    assert [e.relationship for e in result.changes[0].call_edges] == ["MESSAGE_FLOW", "CALLS"]
    prior_graph = graph((("a", "b"), ("b", "c")), end=END - timedelta(days=1))
    match = history(profile(g), [profile(prior_graph)]).matches[0]
    assert match.dependency_similarity == pytest.approx(1 / 3)
    assert match.new_dependencies[0].relationship == "MESSAGE_FLOW"
    assert match.previous_only_dependencies[0].relationship == "CALLS"


def test_available_history_uses_current_knowledge_cutoff_for_late_receipts():
    current = profile(graph(mode=TemporalMode.AVAILABLE))
    prior_graph = graph(end=END - timedelta(days=1), mode=TemporalMode.EVENT)
    prior_fact = fact(prior_graph, available=prior_graph.as_of + timedelta(hours=1))
    prior = replace(
        profile(prior_graph, facts=[prior_fact]),
        available_at=prior_graph.as_of + timedelta(hours=1),
    )
    match = history(current, [prior]).matches[0]
    assert match.temporal_mode is TemporalMode.EVENT
    assert match.as_of == prior_graph.as_of
    assert match.knowledge_as_of == current.graph.as_of
    source = match.state_differences[0].previous_evidence
    assert source.temporal_mode is TemporalMode.EVENT
    assert source.knowledge_as_of == current.graph.as_of
    assert not history(current, [replace(prior, available_at=END + timedelta(seconds=1))]).matches
    assert not history(current, [replace(prior, available_at=None)]).matches


def test_evaluation_ablations_remove_only_the_declared_information():
    from scripts.evaluation.operational_capture import methods

    g = graph((("a", "b"), ("b", "c")))
    before = fact(g, "c", seconds=-30)
    upstream = fact(g, "a", seconds=-25)
    after = fact(g, "c", seconds=-5)
    result = reason_changes(
        g, (before, upstream, after), tuple(signal(g, n) for n in "abc"), key("a")
    )
    ranked = methods(result)
    assert ranked["graph_temporal"][0] == str(before.event.id)
    assert ranked["without_graph"][0] == str(upstream.event.id)
    assert ranked["without_time"][0] == str(after.event.id)
    assert ranked["chronological"][0] == str(after.event.id)
    assert ranked["direct_service"][0] == str(upstream.event.id)
