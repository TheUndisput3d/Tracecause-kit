from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tracecause.api.main import app
from tracecause.cli import main


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_doctor() -> None:
    assert main(["doctor"]) == 0


def test_doctor_requires_database_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("TRACECAUSE_PG_DSN", raising=False)
    assert main(["doctor", "--check-databases"]) == 1
