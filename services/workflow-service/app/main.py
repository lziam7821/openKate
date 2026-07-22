import asyncio
import os
from datetime import timedelta
from typing import Any, Dict, Optional
import httpx
from fastapi import FastAPI, HTTPException, status
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from openkate_common.service_app import instrument_app
from openkate_common.temporal import PlatformHeartbeatWorkflow

app = FastAPI(title="workflow-service", version="0.3.0")
instrument_app(app, "workflow-service", ["workflow"])

EXECUTION_SERVICE_URL = os.getenv("OPENKATE_EXECUTION_SERVICE_URL", "http://127.0.0.1:8004")
TEMPORAL_ADDRESS = os.getenv("OPENKATE_TEMPORAL_ADDRESS", "127.0.0.1:7233")
TEMPORAL_NAMESPACE = os.getenv("OPENKATE_TEMPORAL_NAMESPACE", "openkate")
EXECUTOR_URLS = {
    "ui": os.getenv("OPENKATE_EXECUTOR_UI_URL", "http://127.0.0.1:8011"),
    "api": os.getenv("OPENKATE_EXECUTOR_API_URL", "http://127.0.0.1:8012"),
    "state": os.getenv("OPENKATE_EXECUTOR_STATE_URL", "http://127.0.0.1:8013"),
    "mobile": os.getenv("OPENKATE_EXECUTOR_MOBILE_URL", "http://127.0.0.1:8014"),
    "external": os.getenv("OPENKATE_EXECUTOR_EXTERNAL_URL", "http://127.0.0.1:8015"),
    "quality": os.getenv("OPENKATE_EXECUTOR_QUALITY_URL", "http://127.0.0.1:8016"),
}
active_tasks: Dict[str, asyncio.Task[None]] = {}
TEMPORAL_ENABLED = os.getenv("OPENKATE_TEMPORAL_ENABLED", "false").lower() == "true"
SCENARIO_TASK_QUEUE = "scenario-execution"


def executor_request(run_id: str, step: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "runId": run_id,
        "stepId": step["id"],
        "action": step["action"],
        "input": {**step["input"], **({"deviceId": run["deviceId"]} if step["channel"] == "mobile" else {})},
        "variables": run["_variables"],
        "allowedHosts": run.get("allowedHosts", []),
        "timeoutMs": step["timeoutMs"],
    }

async def cancel_running_steps(run_id: str, context: Dict[str, Any], client: httpx.AsyncClient) -> None:
    run, plan = context["run"], context["plan"]
    steps = {step["id"]: step for step in plan["steps"]}
    for result in run["stepResults"]:
        if result["status"] != "running":
            continue
        step = steps[result["stepId"]]
        try:
            await client.post(f"{EXECUTOR_URLS[step['channel']]}/cancel", json=executor_request(run_id, step, run))
        except httpx.HTTPError:
            pass


class ScenarioExecutionWorkflow:
    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self.client = client

    async def run(self, run_id: str) -> None:
        if self.client is not None:
            await self._run(run_id, self.client)
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            await self._run(run_id, client)

    async def _run(self, run_id: str, client: httpx.AsyncClient) -> None:
        context = await self._context(run_id, client)
        plan = context["plan"]
        steps = {step["id"]: step for step in plan["steps"]}
        while True:
            context = await self._context(run_id, client)
            run = context["run"]
            if run["status"] != "running":
                return
            results = {item["stepId"]: item for item in run["stepResults"]}
            ready = [step_id for step_id in plan["orderedStepIds"] if results[step_id]["status"] != "completed" and all(results[dependency]["status"] == "completed" for dependency in steps[step_id]["dependsOn"])]
            if not ready:
                return
            succeeded = await asyncio.gather(*(self._run_step(run_id, steps[step_id], results[step_id], run, plan, client) for step_id in ready))
            if not all(succeeded):
                return

    async def _run_step(self, run_id: str, step: Dict[str, Any], result: Dict[str, Any], run: Dict[str, Any], plan: Dict[str, Any], client: httpx.AsyncClient) -> bool:
        step_id = step["id"]
        if result["status"] == "running" and not step["idempotent"]:
            await self._abort(run_id, step_id, "recovery_required", "non-idempotent activity requires manual recovery", run, plan, client)
            return False
        if result["status"] == "pending":
            response = await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{step_id}/start")
            if response.is_error:
                await self._abort(run_id, step_id, "dependency_error", self._message(response), run, plan, client)
                return False

        request_payload = executor_request(run_id, step, run)
        attempts = 3 if step["idempotent"] else 1
        response: Optional[httpx.Response] = None
        for attempt in range(attempts):
            try:
                response = await client.post(f"{EXECUTOR_URLS[step['channel']]}/execute", json=request_payload)
            except httpx.HTTPError as error:
                if attempt + 1 == attempts:
                    await self._abort(run_id, step_id, "executor_unavailable", str(error), run, plan, client)
                    return False
                continue
            if response.is_success or response.status_code < 500:
                break
        assert response is not None
        if response.is_error:
            await self._abort(run_id, step_id, "executor_error", self._message(response), run, plan, client)
            return False
        executor_result = response.json()
        completed = await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{step_id}/complete", json={"output": executor_result.get("output", {}), "assertions": executor_result.get("assertions", []), "evidenceRefs": executor_result.get("evidenceRefs", [])})
        if completed.is_error:
            await self._abort(run_id, step_id, "persistence_error", self._message(completed), run, plan, client)
            return False
        return True

    async def _abort(self, run_id: str, step_id: str, category: str, message: str, run: Dict[str, Any], plan: Dict[str, Any], client: httpx.AsyncClient) -> None:
        results = {item["stepId"]: item for item in run["stepResults"]}
        steps = {item["id"]: item for item in plan["steps"]}
        for completed_id in reversed(plan["orderedStepIds"]):
            completed = steps[completed_id]
            if results[completed_id]["status"] != "completed" or not completed.get("compensation"):
                continue
            payload = {"runId": run_id, "stepId": completed_id, "action": completed["compensation"], "input": completed["input"], "variables": run["_variables"], "allowedHosts": run.get("allowedHosts", []), "timeoutMs": completed["timeoutMs"]}
            response = await client.post(f"{EXECUTOR_URLS[completed['channel']]}/execute", json=payload)
            if response.is_success:
                await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{completed_id}/compensated")
        await self._fail(run_id, step_id, category, message, client)

    @staticmethod
    async def _context(run_id: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        response = await client.get(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/context")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="execution run not found")
        response.raise_for_status()
        return response.json()

    @staticmethod
    async def _fail(run_id: str, step_id: str, category: str, message: str, client: httpx.AsyncClient) -> None:
        await client.post(
            f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{step_id}/fail",
            json={"category": category, "message": message[:1000]},
        )

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            body = response.json()
            return str(body.get("detail", body))
        except ValueError:
            return response.text or f"HTTP {response.status_code}"


async def execute_workflow(workflow_id: str, run_id: str) -> None:
    try:
        await ScenarioExecutionWorkflow().run(run_id)
    finally:
        active_tasks.pop(workflow_id, None)


@activity.defn
async def execute_scenario_run(run_id: str) -> None:
    await ScenarioExecutionWorkflow().run(run_id)


@workflow.defn
class DurableScenarioExecutionWorkflow:
    @workflow.run
    async def run(self, run_id: str) -> None:
        await workflow.execute_activity(
            execute_scenario_run,
            run_id,
            start_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"service": "workflow-service", "status": "ready", "activeWorkflows": len(active_tasks)}


@app.post("/internal/v1/platform-heartbeat")
async def platform_heartbeat() -> Dict[str, str]:
    from temporalio.client import Client

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    return await client.execute_workflow(
        PlatformHeartbeatWorkflow.run,
        "workflow-service",
        id=f"platform-heartbeat-{int(asyncio.get_running_loop().time() * 1000)}",
        task_queue="platform-baseline",
    )


@app.post("/internal/v1/runs/{run_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def start_workflow(run_id: str) -> Dict[str, str]:
    workflow_id = f"workflow_{run_id}"
    if TEMPORAL_ENABLED:
        client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
        try:
            await client.start_workflow(DurableScenarioExecutionWorkflow.run, run_id, id=workflow_id, task_queue=SCENARIO_TASK_QUEUE)
        except WorkflowAlreadyStartedError:
            pass
        return {"workflowId": workflow_id, "runId": run_id}
    existing = active_tasks.get(workflow_id)
    if existing and not existing.done():
        return {"workflowId": workflow_id, "runId": run_id}
    active_tasks[workflow_id] = asyncio.create_task(execute_workflow(workflow_id, run_id))
    return {"workflowId": workflow_id, "runId": run_id}


@app.post("/internal/v1/runs/{run_id}/cancel")
async def cancel_workflow(run_id: str) -> Dict[str, str]:
    workflow_id = f"workflow_{run_id}"
    async with httpx.AsyncClient(timeout=3.0) as client:
        context = await client.get(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/context")
        if context.status_code == 404:
            raise HTTPException(status_code=404, detail="execution run not found")
        if context.is_success:
            await cancel_running_steps(run_id, context.json(), client)
    if TEMPORAL_ENABLED:
        client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
        await client.get_workflow_handle(workflow_id).cancel()
    elif task := active_tasks.get(workflow_id):
        task.cancel()
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/cancel")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="execution run not found")
    return {"workflowId": workflow_id, "runId": run_id, "status": "canceling"}
