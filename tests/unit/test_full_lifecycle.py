"""The restart audit must include every saved result, beyond the first API page."""

import hashlib

import httpx
import pytest
from scripts.deployment.full_lifecycle import saved_state


def test_saved_state_follows_episode_and_result_pagination():
    def response(request):
        path, cursor = request.url.path, request.url.params.get("cursor")
        if path == "/api/v1/episodes":
            identifier = "second" if cursor else "first"
            return httpx.Response(
                200,
                json={
                    "items": [{"episode_id": identifier, "active_snapshot_id": identifier}],
                    "next_cursor": None if cursor else "episodes-next",
                },
            )
        if path.startswith("/api/v1/episodes/"):
            identifier = path.split("/")[4] + ("-2" if cursor else "-1")
            return httpx.Response(
                200,
                json={
                    "items": [{"investigation_id": identifier}],
                    "next_cursor": None if cursor else "results-next",
                },
            )
        return httpx.Response(200, content=path.rsplit("/", 1)[-1].encode())

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
        state = saved_state(client)
    assert state["snapshots"] == {"first": "first", "second": "second"}
    assert state["saved_result_hashes"] == {
        value: hashlib.sha256(value.encode()).hexdigest()
        for value in ("first-1", "first-2", "second-1", "second-2")
    }


def test_saved_state_rejects_repeated_cursor():
    def response(request):
        return httpx.Response(200, json={"items": [], "next_cursor": "repeated"})

    with (
        httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client,
        pytest.raises(RuntimeError, match="cursor repeated"),
    ):
        saved_state(client)
