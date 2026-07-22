import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "connector-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("connector_service", MODULE_PATH)
assert spec and spec.loader
connector_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector_service)
client = TestClient(connector_service.app)


def test_github_webhook_validates_signature_and_deduplicates(monkeypatch) -> None:
    connector_service.store.connectors.clear()
    connector_service.store.deliveries.clear()
    monkeypatch.setenv("OPENKATE_WEBHOOK_SECRETS", json.dumps({"vault://github-demo": "webhook-secret"}))
    created = client.post("/internal/v1/projects/project-a/connectors", headers={"X-OpenKATE-Role": "maintainer"}, json={"provider": "github", "repository": "openkate/demo", "secretRef": "vault://github-demo"})
    assert created.status_code == 201
    body = b'{"action":"opened","pull_request":{"number":12,"head":{"sha":"abc123"}}}'
    signature = "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    path = "/internal/v1/webhooks/github/project-a"
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Delivery": "delivery-1", "Content-Type": "application/json"}
    accepted = client.post(path, headers=headers, content=body).json()
    assert accepted["status"] == "accepted"
    assert accepted["pullRequestId"]
    assert connector_service.store.pull_requests[accepted["pullRequestId"]]["headSha"] == "abc123"
    assert client.post(path, headers=headers, content=body).json()["status"] == "duplicate"


def test_webhook_rejects_invalid_signature() -> None:
    connector_service.store.connectors.clear()
    connector_service.store.connectors["connector_1"] = {"id": "connector_1", "projectId": "project-a", "provider": "gitlab", "repository": "openkate/demo", "secretRef": "vault://gitlab-demo", "createdAt": "now"}
    response = client.post("/internal/v1/webhooks/gitlab/project-a", headers={"X-Gitlab-Token": "wrong", "X-Gitlab-Event-UUID": "delivery-2"}, content=b"{}")
    assert response.status_code in {401, 503}


def test_ci_trigger_selects_impacted_scenarios_or_requires_confirmation(monkeypatch) -> None:
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, params):
            assert "project-a" in url
            return httpx.Response(200, json={"items": [{"id": "scenario-payment"}], "fallbackRequired": False})

    connector_service.store.ci_runs.clear()
    monkeypatch.setattr(connector_service.httpx, "AsyncClient", lambda **_: Client())
    triggered = client.post("/internal/v1/ci/projects/project-a/trigger", headers={"X-OpenKATE-Role": "maintainer"}, json={"targets": ["src/payments.py"], "pullRequestId": "pr_1", "commitSha": "abc123"})
    assert triggered.status_code == 202
    assert triggered.json()["status"] == "queued"
    assert triggered.json()["scenarioIds"] == ["scenario-payment"]
    assert client.get(f"/internal/v1/ci/runs/{triggered.json()['id']}/status").json()["commitSha"] == "abc123"


def test_connector_sync_persists_a_resumable_cursor_state() -> None:
    connector_service.store.connectors.clear()
    created = client.post("/internal/v1/projects/project-a/connectors", headers={"X-OpenKATE-Role": "maintainer"}, json={"provider": "gitlab", "repository": "openkate/demo", "secretRef": "vault://gitlab-demo"}).json()
    synced = client.post(f"/internal/v1/connectors/{created['id']}/sync", headers={"X-OpenKATE-Role": "maintainer"})
    assert synced.status_code == 202
    assert synced.json()["repository"] == "openkate/demo"
