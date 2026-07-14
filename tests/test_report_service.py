import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "report-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("report_service", MODULE_PATH)
assert spec and spec.loader
report_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_service)
client = TestClient(report_service.app)


def test_read_model_is_idempotent_and_filters_project_scenarios() -> None:
    report_service.store.models.clear()
    report_service.store.consumed_event_ids.clear()
    event = {
        "eventId": "event-1",
        "eventType": "validation.scenario.created.v1",
        "projectId": "project_checkout",
        "aggregateId": "scenario_checkout",
        "occurredAt": "2026-07-14T00:00:00+00:00",
        "payload": {
            "scenario": {
                "id": "scenario_checkout",
                "projectId": "project_checkout",
                "title": "Checkout paid order",
                "businessGoal": "A paid checkout creates a paid order",
                "status": "approved",
                "riskLevel": "high",
                "tags": ["checkout"],
                "owner": "qa-ada",
                "updatedAt": "2026-07-14T00:00:00+00:00",
            }
        },
    }
    assert client.post("/internal/v1/events", json=event).json() == {"accepted": True}
    assert client.post("/internal/v1/events", json=event).json() == {"accepted": False}

    response = client.get("/internal/v1/projects/project_checkout/scenarios?status=approved&risk=high&tag=checkout&owner=qa-ada")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == "scenario_checkout"
