import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "execution-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("execution_service", MODULE_PATH)
assert spec and spec.loader
execution_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(execution_service)
client = TestClient(execution_service.app)


def reset_store() -> None:
    execution_service.store.plans.clear()
    execution_service.store.runs.clear()
    execution_service.store.leases.clear()
    execution_service.store.idempotency.clear()
    execution_service.store.events.clear()
    execution_service.store.capabilities.clear()
    execution_service.store.save_capabilities(
        "project_checkout",
        [
            {"channel": "ui", "worker": "executor-ui", "capabilities": ["ui.web"], "sdkVersion": "1.0", "contractVersion": "1", "status": "ready", "observedAt": execution_service.store.now()},
            {"channel": "api", "worker": "executor-api", "capabilities": ["api.http"], "sdkVersion": "1.0", "contractVersion": "1", "status": "ready", "observedAt": execution_service.store.now()},
            {"channel": "state", "worker": "executor-state", "capabilities": ["state.postgresql.read_only"], "sdkVersion": "1.0", "contractVersion": "1", "status": "ready", "observedAt": execution_service.store.now()},
        ],
    )


def valid_plan() -> dict:
    return {
        "scenarioVersion": 2,
        "scenarioStatus": "approved",
        "variables": {"sku": "SKU-1"},
        "timeoutMs": 60000,
        "steps": [
            {"id": "place_order", "channel": "ui", "action": "checkout", "input": {"sku": "{{ sku }}"}, "save": {"order.id": "orderId"}},
            {"id": "pay_order", "channel": "api", "action": "request", "dependsOn": ["place_order"], "input": {"path": "/orders/{{ orderId }}/pay"}, "save": {"traceId": "traceId"}},
            {"id": "verify_order", "channel": "state", "action": "query", "dependsOn": ["pay_order"], "input": {"params": {"order_id": "{{ orderId }}"}}},
        ],
    }


def create(payload: dict):
    return client.post("/internal/v1/scenarios/scenario_checkout/execution-plans", headers={"X-OpenKATE-Project-Id": "project_checkout"}, json=payload)


def test_approved_scenario_creates_ordered_cross_channel_plan() -> None:
    reset_store()
    response = create(valid_plan())
    assert response.status_code == 201
    plan = response.json()
    assert plan["orderedStepIds"] == ["place_order", "pay_order", "verify_order"]
    assert plan["scenarioVersion"] == 2
    assert response.headers["etag"] == '"1"'
    assert execution_service.store.events[0]["eventType"] == "execution.plan.created.v1"


def test_plan_rejects_non_approved_scenario_cycle_and_missing_variable() -> None:
    reset_store()
    payload = valid_plan()
    payload["scenarioStatus"] = "draft"
    assert create(payload).status_code == 409

    payload = valid_plan()
    payload["steps"][0]["dependsOn"] = ["verify_order"]
    response = create(payload)
    assert response.status_code == 422
    assert "dependency cycle" in response.json()["detail"]

    payload = valid_plan()
    payload["steps"][1]["dependsOn"] = []
    response = create(payload)
    assert response.status_code == 422
    assert "orderId" in response.json()["detail"]


def test_plan_update_uses_optimistic_lock_and_revalidates_dag() -> None:
    reset_store()
    created = create(valid_plan())
    plan_id = created.json()["id"]
    stale = client.patch(f"/internal/v1/execution-plans/{plan_id}", headers={"If-Match": '"0"'}, json={"timeoutMs": 45000})
    assert stale.status_code == 409
    updated = client.patch(f"/internal/v1/execution-plans/{plan_id}", headers={"If-Match": created.headers["etag"]}, json={"timeoutMs": 45000})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["timeoutMs"] == 45000


def test_plan_rejects_channel_without_ready_executor() -> None:
    reset_store()
    payload = valid_plan()
    payload["steps"][0]["channel"] = "mobile"
    response = create(payload)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "EXECUTOR_CAPABILITY_UNAVAILABLE",
        "message": "executor capability is unavailable",
        "channels": ["mobile"],
    }


def test_capability_discovery_registers_workers_and_missing_channels(monkeypatch) -> None:
    reset_store()
    execution_service.store.capabilities.clear()

    class WorkerClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            channel = next(name for name, base_url in execution_service.EXECUTOR_URLS.items() if url.startswith(base_url))
            return __import__("httpx").Response(200, json={"worker": f"executor-{channel}", "status": "ready", "capabilities": [f"{channel}.test"], "sdkVersion": "1.0", "contractVersion": "1"})

    monkeypatch.setattr(execution_service.httpx, "AsyncClient", WorkerClient)
    response = client.get("/internal/v1/projects/project_capabilities/executor-capabilities")
    assert response.status_code == 200
    items = {item["channel"]: item for item in response.json()["items"]}
    assert items["api"]["worker"] == "executor-api"
    assert items["api"]["sdkVersion"] == "1.0"
    assert items["mobile"]["status"] == "unavailable"


def test_capability_discovery_rejects_incompatible_worker_contract(monkeypatch) -> None:
    reset_store()
    execution_service.store.capabilities.clear()

    class WorkerClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return __import__("httpx").Response(200, json={"worker": "executor-api", "status": "ready", "capabilities": ["api.http"], "sdkVersion": "999.0", "contractVersion": "1"})

    monkeypatch.setattr(execution_service.httpx, "AsyncClient", WorkerClient)
    response = client.get("/internal/v1/projects/project_incompatible/executor-capabilities")
    assert response.status_code == 200
    assert all(item["status"] == "unavailable" for item in response.json()["items"][:3])
