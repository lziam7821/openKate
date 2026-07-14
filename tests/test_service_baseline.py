from fastapi.testclient import TestClient

from openkate_common.service_app import create_service_app


def test_service_baseline_exposes_health_capabilities_metrics_and_request_id() -> None:
    client = TestClient(create_service_app("baseline-service"))
    health = client.get("/health", headers={"X-Request-ID": "request-baseline"})
    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "request-baseline"
    assert client.get("/capabilities").json() == {"service": "baseline-service", "capabilities": []}
    metrics = client.get("/metrics").text
    assert 'service="baseline-service"' in metrics
    assert 'path="/health"' in metrics
