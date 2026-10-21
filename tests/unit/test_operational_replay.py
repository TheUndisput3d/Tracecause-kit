"""Canonical replay identity and bounded, failure-safe materialization."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tests.unit.test_signals import ALERT, EPISODE, WORKSPACE, baseline, fact

from tracecause.operational import replay
from tracecause.telemetry.signals import BUCKET_NS, compute_signals


@pytest.fixture
def source(monkeypatch):
    start = 1_700_000_300_000_000_000
    episode = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        ingestion_status="IMPORTED",
        analysis_start_ns=start,
        event_end=datetime.fromtimestamp((start + 80 * 10**9) / 10**9, UTC),
        manifest_hash="a" * 64,
        data_quality={"operational_application_id": "test"},
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.return_value = episode
    factory.return_value.__enter__.return_value.scalar.side_effect = [True, True]
    factory.begin.return_value.__enter__.return_value.get.return_value = episode
    calls = MagicMock()
    monkeypatch.setattr(replay, "materialize_signals", calls)
    return SimpleNamespace(episode=episode, factory=factory, artifacts=MagicMock(), calls=calls)


@pytest.mark.parametrize("extra_microseconds", [0, 10_635, 999_999])
def test_replay_materializes_final_closed_cutoff_inclusively(source, extra_microseconds):
    episode = source.episode
    episode.event_end = episode.event_end.replace(microsecond=extra_microseconds)
    checkpoint = MagicMock()
    cutoffs = replay.materialize_replay(
        source.factory, source.artifacts, episode.workspace_id, episode.id, checkpoint=checkpoint
    )
    assert cutoffs == tuple(episode.analysis_start_ns + n * BUCKET_NS for n in range(1, 9))
    assert source.calls.call_count == checkpoint.call_count == 8
    assert [call.kwargs["as_of_ns"] for call in source.calls.call_args_list] == list(cutoffs)
    assert all(
        call.kwargs["include_history_buckets"] is False for call in source.calls.call_args_list[:-1]
    )
    assert source.calls.call_args_list[-1].kwargs["include_history_buckets"] is True
    assert episode.data_quality["operational_replay_version"] == replay.REPLAY_VERSION
    assert episode.data_quality["operational_replay_modes"] == ["event"]


def test_complete_source_availability_materializes_both_modes_at_each_cutoff(source):
    episode = source.episode
    source.factory.return_value.__enter__.return_value.scalar.side_effect = [True, False]
    cutoffs = replay.materialize_replay(
        source.factory, source.artifacts, episode.workspace_id, episode.id
    )
    assert source.calls.call_count == 16
    assert [
        (call.kwargs["as_of_ns"], call.kwargs["mode"]) for call in source.calls.call_args_list
    ] == [(cutoff, mode) for cutoff in cutoffs for mode in ("event", "available")]
    assert episode.data_quality["operational_replay_modes"] == ["event", "available"]
    assert sum(call.kwargs["include_history_buckets"] for call in source.calls.call_args_list) == 2


def test_failed_availability_materialization_does_not_advertise_support(source):
    episode = source.episode
    source.factory.return_value.__enter__.return_value.scalar.side_effect = [True, False]
    source.calls.side_effect = [None, ValueError("unknown source availability")]
    with pytest.raises(ValueError, match="unknown source"):
        replay.materialize_replay(
            source.factory, source.artifacts, episode.workspace_id, episode.id
        )
    assert "operational_replay_version" not in episode.data_quality
    assert "operational_replay_modes" not in episode.data_quality


@pytest.mark.parametrize("buckets", [0, 121])
def test_replay_rejects_empty_or_oversized_intervals_before_writes(source, buckets):
    episode = source.episode
    episode.event_end = datetime.fromtimestamp(
        (episode.analysis_start_ns + buckets * BUCKET_NS) / 10**9, UTC
    )
    with pytest.raises(ValueError, match="one to 120"):
        replay.materialize_replay(
            source.factory, source.artifacts, episode.workspace_id, episode.id
        )
    source.calls.assert_not_called()
    assert "operational_replay_version" not in episode.data_quality


def test_failed_materialization_does_not_publish_version(source):
    source.calls.side_effect = RuntimeError("source validation failed")
    episode = source.episode
    with pytest.raises(RuntimeError, match="source validation"):
        replay.materialize_replay(
            source.factory, source.artifacts, episode.workspace_id, episode.id
        )
    assert "operational_replay_version" not in episode.data_quality


def test_replay_rejects_cross_workspace_and_nonoperational_source(source):
    episode = source.episode
    with pytest.raises(ValueError, match="scoped baseline"):
        replay.materialize_replay(source.factory, source.artifacts, uuid4(), episode.id)
    episode.data_quality = {}
    with pytest.raises(ValueError, match="scoped baseline"):
        replay.materialize_replay(
            source.factory, source.artifacts, episode.workspace_id, episode.id
        )
    source.calls.assert_not_called()


def test_available_aggregates_keep_late_source_out_and_match_canonical_request(source):
    """Use the real signal engine; only persistence itself is a unit double."""
    episode = source.episode
    episode.id, episode.workspace_id, episode.analysis_start_ns = EPISODE, WORKSPACE, ALERT
    episode.event_end = datetime.fromtimestamp((ALERT + 80_000_000_000) / 10**9, UTC).replace(
        microsecond=10_635
    )
    cutoff = ALERT + 60_000_000_000
    arrival = datetime.fromtimestamp(cutoff / 10**9, UTC) + timedelta(seconds=5)
    healthy = tuple(
        replace(span, available_at=datetime.fromtimestamp(span.end_ns / 10**9, UTC))
        for span in baseline()
    )
    on_time = fact(
        100,
        end_ns=ALERT + 25_000_000_000,
        status="ERROR",
        available_at=arrival - timedelta(seconds=20),
    )
    late = fact(101, end_ns=ALERT + 35_000_000_000, status="ERROR", available_at=arrival)
    spans = (*healthy, on_time, late)
    reports = {}

    def calculate(*args, **kwargs):
        reports[(kwargs["as_of_ns"], kwargs["mode"])] = compute_signals(
            spans,
            **{
                key: kwargs[key]
                for key in (
                    "workspace_id",
                    "episode_id",
                    "investigation_start_ns",
                    "as_of_ns",
                    "mode",
                    "include_history_buckets",
                )
            },
        )

    source.calls.side_effect = calculate
    source.factory.return_value.__enter__.return_value.scalar.side_effect = [True, False]
    replay.materialize_replay(source.factory, source.artifacts, WORKSPACE, EPISODE)
    stored = reports[(cutoff, "available")]
    aggregate = next(
        item for item in stored.observations if item.id == stored.signals[0].analysis_observation_id
    )
    assert aggregate.sample_count == 1
    assert late.key not in {(trace, span) for trace, span, _ in aggregate.supports}
    event = reports[(cutoff, "event")]
    assert event.signals[0].analysis_observation_id != stored.signals[0].analysis_observation_id
    cursor = cutoff + 10_635_000
    requested = compute_signals(
        spans,
        workspace_id=WORKSPACE,
        episode_id=EPISODE,
        investigation_start_ns=ALERT,
        as_of_ns=cursor // BUCKET_NS * BUCKET_NS,
        mode="available",
    )
    assert requested.signals == stored.signals
    later = reports[(cutoff + BUCKET_NS, "available")]
    later_aggregate = next(
        item for item in later.observations if item.id == later.signals[0].analysis_observation_id
    )
    assert later_aggregate.sample_count == 2
