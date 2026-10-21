from fastapi.testclient import TestClient
from pydantic import SecretStr

from tracecause.api.main import create_app
from tracecause.config.settings import RuntimeSettings


def test_public_mode_rejects_mutations_before_reading_body():
    with TestClient(create_app(RuntimeSettings())) as client:
        response = client.post(
            "/api/v1/imports", content=b"bad", headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "public_read_only"
        assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/v1/config").json()["generation_modes"] == ["template"]


def test_operator_token_and_body_limits_fail_safely():
    settings = RuntimeSettings(public_read_only=False, operator_token=SecretStr("test-only-token"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/auth/check").status_code == 401
        assert (
            client.get("/api/v1/auth/check", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/auth/check", headers={"Authorization": "Bearer test-only-token"}
            ).status_code
            == 200
        )
        response = client.post("/api/v1/investigations", content=b"x" * 65537)
        assert response.status_code == 413
        assert "test-only-token" not in response.text
        assert "pg_password" not in client.get("/api/v1/config").text


def test_streaming_limit_without_content_length_does_not_trust_headers():
    import asyncio

    from tracecause.api.main import Boundaries

    async def scenario():
        chunks = 0
        sent = []

        async def receive():
            nonlocal chunks
            chunks += 1
            return {"type": "http.request", "body": b"x" * 16384, "more_body": True}

        async def send(message):
            sent.append(message)

        async def consumer(scope, receive, send):
            while True:
                await receive()

        app = Boundaries(
            consumer,
            RuntimeSettings(public_read_only=False, operator_token=SecretStr("test-only")),
        )
        await app(
            {"type": "http", "method": "POST", "path": "/api/v1/investigations", "headers": []},
            receive,
            send,
        )
        assert chunks == 5
        assert sent[0]["status"] == 413
        assert b"upload_too_large" in sent[1]["body"]

    asyncio.run(scenario())


def test_missing_routes_and_wrong_methods_share_safe_error_envelope():
    with TestClient(create_app(RuntimeSettings())) as client:
        for path, status in (("/api/v1/missing", 404), ("/api/v1/imports", 405)):
            response = client.get(path)
            assert response.status_code == status
            assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_chunked_json_route_returns_413():
    settings = RuntimeSettings(public_read_only=False, operator_token=SecretStr("test-only"))
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/investigations",
            content=iter([b"x" * 16384] * 5),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-only",
                "Idempotency-Key": "chunked",
            },
        )
        assert "content-length" not in response.request.headers
        assert response.status_code == 413, response.text
