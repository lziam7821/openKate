import asyncio
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import httpx


MODULE_PATH = Path(__file__).parents[1] / "services" / "workflow-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("workflow_service", MODULE_PATH)
assert spec and spec.loader
workflow_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow_service)


def context() -> dict:
    steps = [
        {"id": "place_order", "channel": "ui", "action": "checkout", "dependsOn": [], "input": {}, "save": {"orderId": "orderId"}, "timeoutMs": 1000, "idempotent": True},
        {"id": "pay_order", "channel": "api", "action": "request", "dependsOn": ["place_order"], "input": {"url": "https://payments.test/{{ orderId }}"}, "save": {}, "timeoutMs": 1000, "idempotent": True},
        {"id": "verify_order", "channel": "state", "action": "query", "dependsOn": ["pay_order"], "input": {}, "save": {}, "timeoutMs": 1000, "idempotent": True},
    ]
    return {
        "run": {
            "id": "run-1",
            "status": "running",
            "_variables": {"orderId": "order-42"},
            "allowedHosts": ["payments.test"],
            "stepResults": [
                {"stepId": "place_order", "status": "completed"},
                {"stepId": "pay_order", "status": "pending"},
                {"stepId": "verify_order", "status": "pending"},
            ],
        },
        "plan": {"orderedStepIds": [step["id"] for step in steps], "steps": steps},
    }


def test_workflow_recovers_completed_steps_and_retries_idempotent_activity() -> None:
    state = context()
    calls = {"ui": 0, "api": 0, "state": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/context"):
            return httpx.Response(200, json=deepcopy(state))
        if path.endswith("/start"):
            step_id = path.split("/")[-2]
            next(item for item in state["run"]["stepResults"] if item["stepId"] == step_id)["status"] = "running"
            return httpx.Response(200, json={})
        if path.endswith("/complete"):
            step_id = path.split("/")[-2]
            next(item for item in state["run"]["stepResults"] if item["stepId"] == step_id)["status"] = "completed"
            if all(item["status"] == "completed" for item in state["run"]["stepResults"]):
                state["run"]["status"] = "completed"
            return httpx.Response(200, json={})
        if request.url.host == "127.0.0.1" and request.url.port == 8012:
            calls["api"] += 1
            if calls["api"] == 1:
                return httpx.Response(503, json={"detail": "temporary failure"})
            return httpx.Response(200, json={"output": {"status": "PAID"}, "assertions": [], "evidenceRefs": ["http"]})
        if request.url.host == "127.0.0.1" and request.url.port == 8013:
            calls["state"] += 1
            return httpx.Response(200, json={"output": {"rows": [{"status": "PAID"}]}, "assertions": [], "evidenceRefs": ["query"]})
        if request.url.host == "127.0.0.1" and request.url.port == 8011:
            calls["ui"] += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run():
        try:
            await workflow_service.ScenarioExecutionWorkflow(client).run("run-1")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == {"ui": 0, "api": 2, "state": 1}
    assert state["run"]["status"] == "completed"


def test_workflow_does_not_replay_running_non_idempotent_activity() -> None:
    state = context()
    state["plan"]["steps"][1]["idempotent"] = False
    state["run"]["stepResults"][1]["status"] = "running"
    failures = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=state)
        if request.url.path.endswith("/fail"):
            failures.append(request.read().decode())
            return httpx.Response(200, json={})
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run():
        try:
            await workflow_service.ScenarioExecutionWorkflow(client).run("run-1")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert len(failures) == 1
    assert "recovery_required" in failures[0]


def test_workflow_executes_independent_steps_in_parallel() -> None:
    state = context()
    state["plan"]["steps"] = [
        {"id": "left", "channel": "api", "action": "request", "dependsOn": [], "input": {}, "save": {}, "timeoutMs": 1000, "idempotent": True},
        {"id": "right", "channel": "api", "action": "request", "dependsOn": [], "input": {}, "save": {}, "timeoutMs": 1000, "idempotent": True},
    ]
    state["plan"]["orderedStepIds"] = ["left", "right"]
    state["run"]["stepResults"] = [{"stepId": "left", "status": "pending"}, {"stepId": "right", "status": "pending"}]
    timeline = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/context"):
            return httpx.Response(200, json=deepcopy(state))
        if path.endswith("/start"):
            step_id = path.split("/")[-2]
            next(item for item in state["run"]["stepResults"] if item["stepId"] == step_id)["status"] = "running"
            return httpx.Response(200, json={})
        if path.endswith("/complete"):
            step_id = path.split("/")[-2]
            next(item for item in state["run"]["stepResults"] if item["stepId"] == step_id)["status"] = "completed"
            if all(item["status"] == "completed" for item in state["run"]["stepResults"]):
                state["run"]["status"] = "completed"
            return httpx.Response(200, json={})
        step_id = json.loads(request.content)["stepId"]
        timeline.append(f"start:{step_id}")
        await asyncio.sleep(0)
        timeline.append(f"end:{step_id}")
        return httpx.Response(200, json={"output": {}, "assertions": [], "evidenceRefs": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(workflow_service.ScenarioExecutionWorkflow(client).run("run-1"))
    asyncio.run(client.aclose())
    assert set(timeline[:2]) == {"start:left", "start:right"}
    assert state["run"]["status"] == "completed"


def test_workflow_compensates_completed_steps_before_failure() -> None:
    state = context()
    state["plan"]["steps"][0]["compensation"] = "undo_checkout"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(workflow_service.ScenarioExecutionWorkflow(client)._abort("run-1", "pay_order", "executor_error", "failed", state["run"], state["plan"], client))
    asyncio.run(client.aclose())
    assert any(path.endswith("/compensated") for path in calls)
    assert any(path.endswith("/execute") for path in calls)
