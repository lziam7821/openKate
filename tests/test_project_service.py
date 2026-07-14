from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_viewer_cannot_create_project() -> None:
    response = client.post("/internal/v1/workspaces/workspace_demo/projects", json={"name": "Demo"})
    assert response.status_code == 403


def test_owner_can_create_project_and_environment() -> None:
    headers = {"X-OpenKATE-Role": "owner"}
    project = client.post(
        "/internal/v1/workspaces/workspace_demo/projects",
        headers=headers,
        json={"name": "Checkout", "description": "Business validation"},
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    environment = client.post(
        f"/internal/v1/projects/{project_id}/environments",
        headers=headers,
        json={"name": "Staging", "base_url": "https://staging.example.test", "write_policy": "read_only"},
    )
    assert environment.status_code == 201
    assert environment.json()["write_policy"] == "read_only"
    audit = client.get(f"/internal/v1/projects/{project_id}/audit-logs")
    assert len(audit.json()) == 2


def test_environment_exposes_only_connection_and_resource_references() -> None:
    headers = {"X-OpenKATE-Role": "owner"}
    project = client.post("/internal/v1/workspaces/workspace_demo/projects", headers=headers, json={"name": "Execution"}).json()
    environment = client.post(
        f"/internal/v1/projects/{project['id']}/environments",
        headers=headers,
        json={
            "name": "Execution staging",
            "base_url": "https://shop.test",
            "allowed_hosts": ["shop.test", "payments.test"],
            "account_refs": ["vault://accounts/qa-1"],
            "data_set_refs": ["dataset://checkout-1"],
            "secret_refs": {"database": "vault://databases/staging"},
        },
    )
    assert environment.status_code == 201
    listed = client.get(f"/internal/v1/projects/{project['id']}/environments").json()
    assert listed[0]["allowed_hosts"] == ["shop.test", "payments.test"]
    assert "password" not in str(listed).lower()
