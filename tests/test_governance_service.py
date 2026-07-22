import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "governance-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("governance_service", MODULE_PATH)
assert spec and spec.loader
governance_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(governance_service)
client = TestClient(governance_service.app)


def test_failure_classification_keeps_auditable_correction_history() -> None:
    governance_service.store.items.clear()
    path = "/internal/v1/failures/failure-pay/classification"
    first = client.post(path, headers={"X-OpenKATE-Actor": "qa-ada"}, json={"category": "environment", "reason": "payment sandbox unavailable"})
    assert first.status_code == 200
    corrected = client.post(path, headers={"X-OpenKATE-Actor": "reviewer-lin"}, json={"category": "product", "reason": "payment callback regression"})
    assert corrected.json()["category"] == "product"
    assert [(item["actor"], item["from"], item["to"]) for item in corrected.json()["audit"]] == [("qa-ada", "unknown", "environment"), ("reviewer-lin", "environment", "product")]


def test_badcase_keeps_run_evidence_and_manual_correction() -> None:
    governance_service.badcase_store.items.clear()
    response = client.post(
        "/internal/v1/runs/run-payment/badcases",
        headers={"X-OpenKATE-Actor": "qa-ada"},
        json={"evidenceRefs": ["asset://trace-1"], "description": "退款金额未校验"},
    )
    assert response.status_code == 201
    badcase = response.json()
    assert governance_service.badcase_store.get(badcase["id"]) == badcase
