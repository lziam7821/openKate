import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "validation-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("validation_service", MODULE_PATH)
assert spec and spec.loader
validation_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation_service)
client = TestClient(validation_service.app)


def reset_store() -> None:
    validation_service.store.scenarios.clear()
    validation_service.store.events.clear()


def create_scenario(role: str = "developer") -> dict:
    response = client.post(
        "/internal/v1/projects/project_checkout/scenarios",
        headers={"X-OpenKATE-Role": role, "X-OpenKATE-Actor": "qa-ada"},
        json={
            "title": "Checkout paid order",
            "businessGoal": "A paid checkout creates a paid order",
            "actors": ["buyer", "payment service"],
            "preconditions": ["An in-stock product exists"],
            "riskLevel": "high",
            "invariants": ["Paid amount equals checkout total"],
            "risks": [{"title": "Duplicate charge", "level": "critical"}],
            "evidencePoints": [{"channel": "state", "target": "orders", "observation": "Observe payment status", "assertions": [{"path": "status", "operator": "equals", "expected": "PAID"}]}],
            "tags": ["checkout", "payment"],
        },
    )
    assert response.status_code == 201
    return response.json()


def etag(response) -> str:
    return response.headers["etag"]


def test_scenario_review_reject_version_and_approve_flow() -> None:
    reset_store()
    scenario = create_scenario()
    scenario_id = scenario["id"]

    submitted = client.post(
        f"/internal/v1/scenarios/{scenario_id}/submit-review",
        headers={"X-OpenKATE-Role": "developer", "If-Match": '"1"'},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"

    comment = client.post(
        f"/internal/v1/scenarios/{scenario_id}/reviews",
        headers={"X-OpenKATE-Role": "reviewer", "X-OpenKATE-Actor": "reviewer-lin", "If-Match": etag(submitted)},
        json={"content": "Please cover the duplicate charge risk."},
    )
    assert comment.status_code == 201
    assert comment.json()["reviews"][0]["author"] == "reviewer-lin"
    assert comment.json()["reviews"][0]["scenarioVersion"] == 1

    rejected = client.post(
        f"/internal/v1/scenarios/{scenario_id}/reject",
        headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(comment)},
        json={"reason": "Evidence is incomplete"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    updated = client.patch(
        f"/internal/v1/scenarios/{scenario_id}",
        headers={"X-OpenKATE-Role": "developer", "If-Match": etag(rejected)},
        json={"evidencePoints": [{"channel": "api", "target": "payments", "observation": "Observe payment response", "assertions": [{"path": "status", "operator": "equals", "expected": "captured"}]}]},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    assert updated.json()["version"] == 2

    resubmitted = client.post(f"/internal/v1/scenarios/{scenario_id}/submit-review", headers={"X-OpenKATE-Role": "developer", "If-Match": etag(updated)})
    approved = client.post(f"/internal/v1/scenarios/{scenario_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(resubmitted)})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert [event["eventType"] for event in validation_service.store.events] == [
        "validation.scenario.created.v1",
        "validation.scenario.review.requested.v1",
        "validation.scenario.rejected.v1",
        "validation.scenario.versioned.v1",
        "validation.scenario.review.requested.v1",
        "validation.scenario.approved.v1",
    ]


def test_approved_scenario_is_versioned_and_stale_edits_conflict() -> None:
    reset_store()
    scenario = create_scenario()
    scenario_id = scenario["id"]
    submitted = client.post(f"/internal/v1/scenarios/{scenario_id}/submit-review", headers={"X-OpenKATE-Role": "developer", "If-Match": '"1"'})
    approved = client.post(f"/internal/v1/scenarios/{scenario_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(submitted)})

    stale = client.patch(f"/internal/v1/scenarios/{scenario_id}", headers={"X-OpenKATE-Role": "developer", "If-Match": '"1"'}, json={"title": "Old edit"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "SCENARIO_VERSION_CONFLICT"

    versioned = client.patch(f"/internal/v1/scenarios/{scenario_id}", headers={"X-OpenKATE-Role": "developer", "If-Match": etag(approved)}, json={"title": "Checkout with payment retry"})
    assert versioned.status_code == 200
    assert versioned.json()["status"] == "draft"
    assert versioned.json()["version"] == 2

    versions = client.get(f"/internal/v1/scenarios/{scenario_id}/versions").json()
    assert [(item["version"], item["status"]) for item in versions] == [(1, "approved"), (2, "draft")]
    diff = client.get(f"/internal/v1/scenarios/{scenario_id}/diff?fromVersion=1&toVersion=2").json()
    assert diff["changes"] == [{"field": "title", "from": "Checkout paid order", "to": "Checkout with payment retry"}]


def test_permissions_filters_and_illegal_transitions() -> None:
    reset_store()
    forbidden = client.post(
        "/internal/v1/projects/project_checkout/scenarios",
        headers={"X-OpenKATE-Role": "viewer"},
        json={"title": "Denied", "businessGoal": "Denied", "actors": ["buyer"], "riskLevel": "low"},
    )
    assert forbidden.status_code == 403
    scenario = create_scenario()
    scenario_id = scenario["id"]
    assert client.post(f"/internal/v1/scenarios/{scenario_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "If-Match": '"1"'}).status_code == 409
    assert client.post(f"/internal/v1/scenarios/{scenario_id}/submit-review", headers={"X-OpenKATE-Role": "reviewer", "If-Match": '"1"'}).status_code == 403

    listed = client.get("/internal/v1/projects/project_checkout/scenarios?status=draft&risk=high&tag=payment&owner=qa-ada")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_review_resolution_and_approved_terminal_transitions() -> None:
    reset_store()
    scenario = create_scenario()
    scenario_id = scenario["id"]
    submitted = client.post(f"/internal/v1/scenarios/{scenario_id}/submit-review", headers={"X-OpenKATE-Role": "developer", "If-Match": '"1"'})
    review = client.post(
        f"/internal/v1/scenarios/{scenario_id}/reviews",
        headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(submitted)},
        json={"content": "Confirm payment callback evidence"},
    )
    review_id = review.json()["reviews"][0]["id"]
    resolved = client.patch(
        f"/internal/v1/scenarios/{scenario_id}/reviews/{review_id}",
        headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(review)},
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["reviews"][0]["status"] == "resolved"
    approved = client.post(f"/internal/v1/scenarios/{scenario_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(resolved)})
    deprecated = client.post(f"/internal/v1/scenarios/{scenario_id}/deprecate", headers={"X-OpenKATE-Role": "owner", "If-Match": etag(approved)})
    assert deprecated.status_code == 200
    assert deprecated.json()["status"] == "deprecated"

    second = create_scenario()
    submitted = client.post(f"/internal/v1/scenarios/{second['id']}/submit-review", headers={"X-OpenKATE-Role": "developer", "If-Match": '"1"'})
    approved = client.post(f"/internal/v1/scenarios/{second['id']}/approve", headers={"X-OpenKATE-Role": "reviewer", "If-Match": etag(submitted)})
    archived = client.post(f"/internal/v1/scenarios/{second['id']}/archive", headers={"X-OpenKATE-Role": "owner", "If-Match": etag(approved)})
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
