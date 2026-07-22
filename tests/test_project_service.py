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


def test_owner_can_manage_project_device_pools() -> None:
    headers = {"X-OpenKATE-Role": "owner"}
    project = client.post("/internal/v1/workspaces/workspace_demo/projects", headers=headers, json={"name": "Mobile"}).json()
    created = client.post(f"/internal/v1/projects/{project['id']}/device-pools", headers=headers, json={"name": "Android", "deviceIds": ["emulator-1", "emulator-1", "emulator-2"]})
    assert created.status_code == 201
    assert created.json()["deviceIds"] == ["emulator-1", "emulator-2"]
    assert client.get(f"/internal/v1/projects/{project['id']}/device-pools").json()[0]["name"] == "Android"


def test_owner_can_manage_connection_profiles_without_exposing_secrets() -> None:
    headers = {"X-OpenKATE-Role": "owner"}
    project = client.post("/internal/v1/workspaces/workspace_demo/projects", headers=headers, json={"name": "Connections"}).json()
    created = client.post(f"/internal/v1/projects/{project['id']}/connection-profiles", headers=headers, json={"name": "Trace API", "kind": "trace", "endpoint": "https://trace.test/api", "secretRef": "vault://trace/token"})
    assert created.status_code == 201
    assert created.json()["secretRef"] == "vault://trace/token"
    assert "token-value" not in str(client.get(f"/internal/v1/projects/{project['id']}/connection-profiles").json())


def test_owner_can_create_workspace_and_manage_project_members_with_actor_audit() -> None:
    headers = {"X-OpenKATE-Role": "owner", "X-OpenKATE-Actor": "owner-ada"}
    workspace = client.post("/internal/v1/workspaces", headers=headers, json={"name": "Payments"})
    assert workspace.status_code == 201
    project = client.post(f"/internal/v1/workspaces/{workspace.json()['id']}/projects", headers=headers, json={"name": "Checkout"})
    project_id = project.json()["id"]
    member = client.post(f"/internal/v1/projects/{project_id}/members", headers=headers, json={"user_id": "reviewer-lin", "role": "reviewer"})
    assert member.status_code == 201
    updated = client.patch(f"/internal/v1/projects/{project_id}/members/reviewer-lin", headers=headers, json={"role": "developer"})
    assert updated.json()["role"] == "developer"
    audit = client.get(f"/internal/v1/projects/{project_id}/audit-logs", headers=headers).json()
    assert {item["actor"] for item in audit} == {"owner-ada"}


def test_project_membership_controls_management_and_audits_lifecycle() -> None:
    owner = {"X-OpenKATE-Role": "owner", "X-OpenKATE-Actor": "owner-kai"}
    project = client.post("/internal/v1/workspaces/workspace_demo/projects", headers=owner, json={"name": "Membership"}).json()
    project_id = project["id"]
    client.post(f"/internal/v1/projects/{project_id}/members", headers=owner, json={"user_id": "maintainer-lee", "role": "maintainer"})
    client.post(f"/internal/v1/projects/{project_id}/members", headers=owner, json={"user_id": "viewer-mo", "role": "viewer"})
    environment = client.post(
        f"/internal/v1/projects/{project_id}/environments",
        headers=owner,
        json={"name": "Staging", "base_url": "https://staging.test", "write_policy": "read_only"},
    ).json()

    viewer = {"X-OpenKATE-Role": "owner", "X-OpenKATE-Actor": "viewer-mo"}
    denied = client.patch(f"/internal/v1/projects/{project_id}/environments/{environment['id']}", headers=viewer, json={"name": "Denied"})
    assert denied.status_code == 403

    maintainer = {"X-OpenKATE-Role": "maintainer", "X-OpenKATE-Actor": "maintainer-lee"}
    updated = client.patch(
        f"/internal/v1/projects/{project_id}/environments/{environment['id']}",
        headers=maintainer,
        json={"name": "Pre-production", "write_policy": "approval_required"},
    )
    assert updated.status_code == 200
    assert updated.json()["write_policy"] == "approval_required"
    assert client.post(f"/internal/v1/projects/{project_id}/members", headers=maintainer, json={"user_id": "other", "role": "viewer"}).status_code == 403

    assert client.delete(f"/internal/v1/projects/{project_id}/members/viewer-mo", headers=owner).status_code == 204
    archived = client.post(f"/internal/v1/projects/{project_id}/archive", headers=owner)
    assert archived.status_code == 200
    assert archived.json()["archivedAt"] is not None
    actions = {item["action"] for item in client.get(f"/internal/v1/projects/{project_id}/audit-logs", headers=owner).json()}
    assert {"environment.updated", "member.removed", "project.archived"}.issubset(actions)
