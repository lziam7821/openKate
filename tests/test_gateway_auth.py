import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "gateway-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("gateway_service", MODULE_PATH)
assert spec and spec.loader
gateway_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_service)
client = TestClient(gateway_service.app)
TEST_SECRET = "test-secret-that-is-at-least-thirty-two-bytes-long"


def token(role: str = "owner", subject: str = "user-42") -> str:
    return jwt.encode(
        {
            "sub": subject,
            "name": "Ada",
            "email": "ada@example.test",
            "role": role,
            "iss": "https://identity.example.test",
            "aud": "openkate",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )


def auth_headers(role: str = "owner") -> dict[str, str]:
    return {"Authorization": f"Bearer {token(role)}"}


def configure_auth(monkeypatch) -> None:
    monkeypatch.setenv("OPENKATE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("OPENKATE_OIDC_ISSUER", "https://identity.example.test")
    monkeypatch.setenv("OPENKATE_OIDC_AUDIENCE", "openkate")
    monkeypatch.delenv("OPENKATE_OIDC_JWKS_URL", raising=False)


def test_api_rejects_unauthenticated_and_invalid_tokens(monkeypatch) -> None:
    configure_auth(monkeypatch)
    missing = client.get("/api/v1/me")
    invalid = client.get("/api/v1/me", headers={"Authorization": "Bearer invalid"})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


def test_me_returns_verified_identity_and_request_id(monkeypatch) -> None:
    configure_auth(monkeypatch)
    response = client.get("/api/v1/me", headers={**auth_headers(), "X-Request-ID": "request-42"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-42"
    assert response.json() == {"id": "user-42", "name": "Ada", "email": "ada@example.test", "role": "owner", "roles": ["owner"]}


def test_gateway_uses_verified_identity_for_public_project_routes(monkeypatch) -> None:
    configure_auth(monkeypatch)
    calls: list[tuple[str, str, dict[str, str], object]] = []

    async def fake_upstream(url, method, path, request, payload=None, extra_headers=None):
        identity = request.state.identity
        calls.append((method, path, identity, payload))
        if path == "/internal/v1/workspaces":
            return httpx.Response(201, json={"id": "workspace_1", "name": payload["name"]})
        if path.endswith("/members"):
            return httpx.Response(201, json={"userId": payload["user_id"], "role": payload["role"]})
        return httpx.Response(200, json={"id": "project_1", "name": "Updated"})

    monkeypatch.setattr(gateway_service, "upstream", fake_upstream)
    spoofed = {**auth_headers(), "X-OpenKATE-Role": "viewer", "X-OpenKATE-Actor": "attacker"}
    assert client.post("/api/v1/workspaces", headers=spoofed, json={"name": "Team"}).status_code == 201
    assert client.get("/api/v1/projects/project_1", headers=spoofed).status_code == 200
    assert client.patch("/api/v1/projects/project_1", headers=spoofed, json={"name": "Updated"}).status_code == 200
    assert client.post("/api/v1/projects/project_1/members", headers=spoofed, json={"user_id": "user-9", "role": "viewer"}).status_code == 201
    assert client.patch("/api/v1/projects/project_1/members/user-9", headers=spoofed, json={"role": "developer"}).status_code == 200
    assert all(call[2]["id"] == "user-42" and call[2]["role"] == "owner" for call in calls)


def test_failed_workflow_start_cancels_run_and_releases_lease(monkeypatch) -> None:
    configure_auth(monkeypatch)
    calls: list[tuple[str, str]] = []

    async def fake_upstream(url, method, path, request, payload=None, extra_headers=None):
        calls.append((method, path))
        if url == gateway_service.VALIDATION_SERVICE_URL:
            return httpx.Response(200, json={"id": "scenario-1", "projectId": "project-1", "version": 1, "status": "approved"})
        if url == gateway_service.PROJECT_SERVICE_URL:
            return httpx.Response(200, json={"id": "env-1", "allowed_hosts": [], "account_refs": [], "data_set_refs": []})
        if path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "run-1", "status": "canceled"})
        return httpx.Response(202, json={"id": "run-1", "status": "running"})

    class FailedWorkflowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url):
            return httpx.Response(503, json={"detail": "workflow unavailable"})

    monkeypatch.setattr(gateway_service, "upstream", fake_upstream)
    monkeypatch.setattr(gateway_service.httpx, "AsyncClient", FailedWorkflowClient)
    response = client.post(
        "/api/v1/scenarios/scenario-1/runs",
        headers=auth_headers(),
        json={"planId": "plan-1", "environmentId": "env-1"},
    )
    assert response.status_code == 503
    assert ("POST", "/internal/v1/runs/run-1/cancel") in calls
