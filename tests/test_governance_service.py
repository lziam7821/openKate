import importlib.util
from copy import deepcopy
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


def test_high_risk_rule_requires_replay_two_approvals_and_can_rollback() -> None:
    governance_service.badcase_store.items.clear()
    governance_service.rule_store.items.clear()
    governance_service.historical_contexts.clear()
    governance_service.historical_contexts.update({
        "run-a": {"run": {"id": "run-a", "projectId": "project-payment", "status": "failed", "stepResults": [{"status": "failed", "outputSummary": {"message": "when refund validation failed"}}]}, "plan": {}},
        "run-b": {"run": {"id": "run-b", "projectId": "project-payment", "status": "completed", "stepResults": [{"status": "completed", "outputSummary": {}}]}, "plan": {}},
    })
    historical_before_replay = deepcopy(governance_service.historical_contexts)
    badcase = client.post("/internal/v1/runs/run-payment/badcases", headers={"X-OpenKATE-Actor": "qa-ada"}, json={"evidenceRefs": ["asset://trace-1"], "description": "退款金额未校验"}).json()
    draft = client.post(f"/internal/v1/badcases/{badcase['id']}/rule-drafts", headers={"X-OpenKATE-Actor": "qa-ada", "X-OpenKATE-Role": "developer"}, json={"scope": "payment refunds", "expectedEffect": "block invalid refund totals", "riskLevel": "high", "projectId": "project-payment"})
    assert draft.status_code == 201
    rule_id = draft.json()["id"]
    assert client.post(f"/internal/v1/rules/{rule_id}/review", headers={"X-OpenKATE-Role": "reviewer"}, json={}).json()["status"] == "in_review"
    assert client.post(f"/internal/v1/rules/{rule_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "X-OpenKATE-Actor": "reviewer-1"}).json()["status"] == "in_review"
    approved = client.post(f"/internal/v1/rules/{rule_id}/approve", headers={"X-OpenKATE-Role": "maintainer", "X-OpenKATE-Actor": "reviewer-2"})
    assert approved.json()["status"] == "approved"
    replay = client.post(f"/internal/v1/rules/{rule_id}/replay", headers={"X-OpenKATE-Role": "reviewer"}, json={"runIds": ["run-a", "run-a", "run-b"]})
    assert replay.json()["runIds"] == ["run-a", "run-b"]
    assert replay.json()["runSnapshot"][0]["failed"] is True
    assert governance_service.historical_contexts == historical_before_replay
    published = client.post(f"/internal/v1/rules/{rule_id}/publish", headers={"X-OpenKATE-Role": "maintainer"})
    assert published.json()["activeVersion"] == 1
    assert client.get("/internal/v1/projects/project-payment/rules/published").json()["items"][0]["id"] == rule_id
    assert client.get(f"/internal/v1/rules/{rule_id}/metrics").json()["hitRate"] == 0.5
    assert client.post(f"/internal/v1/rules/{rule_id}/rollback", headers={"X-OpenKATE-Role": "maintainer"}).json()["activeVersion"] is None


def test_rule_author_cannot_approve_and_revisions_are_immutable() -> None:
    governance_service.badcase_store.items.clear()
    governance_service.rule_store.items.clear()
    badcase = client.post("/internal/v1/runs/run-1/badcases", headers={"X-OpenKATE-Actor": "author"}, json={"evidenceRefs": ["asset://trace-1"], "description": "税率显示错误"}).json()
    rule_id = client.post(f"/internal/v1/badcases/{badcase['id']}/rule-drafts", headers={"X-OpenKATE-Actor": "author", "X-OpenKATE-Role": "developer"}, json={"scope": "invoices", "expectedEffect": "validate tax", "riskLevel": "low"}).json()["id"]
    client.post(f"/internal/v1/rules/{rule_id}/review", headers={"X-OpenKATE-Role": "reviewer"}, json={})
    assert client.post(f"/internal/v1/rules/{rule_id}/approve", headers={"X-OpenKATE-Role": "reviewer", "X-OpenKATE-Actor": "author"}).status_code == 403
    revised = client.post(f"/internal/v1/rules/{rule_id}/review", headers={"X-OpenKATE-Role": "reviewer", "X-OpenKATE-Actor": "reviewer"}, json={"decision": "changes_requested", "content": "Validate tax rate against invoice country."})
    assert [item["version"] for item in revised.json()["versions"]] == [1, 2]
