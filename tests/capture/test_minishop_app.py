"""Checks requiring the optional OpenTelemetry capture dependencies."""

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ["SERVICE_ROLE"] = "payment"

from demo.minishop import app as minishop_app  # noqa: E402


def test_expiring_control_defaults_to_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(minishop_app.time, "monotonic", lambda: now[0])
    state = minishop_app.FaultState()
    state.set("payment_error", 5)
    assert state.active() == "payment_error"
    assert [state.alternate_failure() for _ in range(4)] == [False, True, False, True]
    now[0] = 105.1
    assert state.active() == "healthy"


def test_private_control_rejects_bad_token_and_resets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "token"
    secret.write_text("sample-secret\n")
    monkeypatch.setattr(minishop_app, "CONTROL_TOKEN_FILE", secret)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            minishop_app.control(
                minishop_app.Control(mode="payment_error", ttl_seconds=5), x_capture_token="wrong"
            )
        )
    assert exc.value.status_code == 403
    result = asyncio.run(
        minishop_app.control(
            minishop_app.Control(mode="payment_error", ttl_seconds=5),
            x_capture_token="sample-secret",
        )
    )
    assert result == {"status": "set", "mode": "payment_error"}
    assert minishop_app.state.active() == "payment_error"
    asyncio.run(
        minishop_app.control(
            minishop_app.Control(mode="healthy", ttl_seconds=1), x_capture_token="sample-secret"
        )
    )
    assert minishop_app.state.active() == "healthy"


def test_role_rejects_other_fault_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "token"
    secret.write_text("sample-secret")
    monkeypatch.setattr(minishop_app, "CONTROL_TOKEN_FILE", secret)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            minishop_app.control(
                minishop_app.Control(mode="catalog_error", ttl_seconds=5),
                x_capture_token="sample-secret",
            )
        )
    assert exc.value.status_code == 400


def test_dependency_request_rejects_external_url() -> None:
    with pytest.raises(RuntimeError, match="not allowlisted"):
        asyncio.run(minishop_app._request("GET", "http://example.com/anything"))
