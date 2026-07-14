import asyncio
import os
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, status

app = FastAPI(title="workflow-service", version="0.3.0")

EXECUTION_SERVICE_URL = os.getenv("OPENKATE_EXECUTION_SERVICE_URL", "http://127.0.0.1:8004")
EXECUTOR_URLS = {
    "ui": os.getenv("OPENKATE_EXECUTOR_UI_URL", "http://127.0.0.1:8011"),
    "api": os.getenv("OPENKATE_EXECUTOR_API_URL", "http://127.0.0.1:8012"),
    "state": os.getenv("OPENKATE_EXECUTOR_STATE_URL", "http://127.0.0.1:8013"),
}
active_tasks: Dict[str, asyncio.Task[None]] = {}


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
        for step_id in plan["orderedStepIds"]:
            context = await self._context(run_id, client)
            run = context["run"]
            if run["status"] != "running":
                return
            result = next(item for item in run["stepResults"] if item["stepId"] == step_id)
            if result["status"] == "completed":
                continue
            step = steps[step_id]
            if result["status"] == "running" and not step["idempotent"]:
                await self._fail(run_id, step_id, "recovery_required", "non-idempotent activity requires manual recovery", client)
                return
            if result["status"] == "pending":
                response = await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{step_id}/start")
                if response.is_error:
                    await self._fail(run_id, step_id, "dependency_error", self._message(response), client)
                    return

            executor_request = {
                "runId": run_id,
                "stepId": step_id,
                "action": step["action"],
                "input": step["input"],
                "variables": run["_variables"],
                "allowedHosts": run.get("allowedHosts", []),
                "timeoutMs": step["timeoutMs"],
            }
            attempts = 3 if step["idempotent"] else 1
            response: Optional[httpx.Response] = None
            for attempt in range(attempts):
                try:
                    response = await client.post(f"{EXECUTOR_URLS[step['channel']]}/execute", json=executor_request)
                except httpx.HTTPError as error:
                    if attempt + 1 == attempts:
                        await self._fail(run_id, step_id, "executor_unavailable", str(error), client)
                        return
                    continue
                if response.is_success or response.status_code < 500:
                    break
            assert response is not None
            if response.is_error:
                await self._fail(run_id, step_id, "executor_error", self._message(response), client)
                return
            executor_result = response.json()
            completed = await client.post(
                f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/steps/{step_id}/complete",
                json={"output": executor_result.get("output", {}), "assertions": executor_result.get("assertions", []), "evidenceRefs": executor_result.get("evidenceRefs", [])},
            )
            if completed.is_error:
                await self._fail(run_id, step_id, "persistence_error", self._message(completed), client)
                return

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


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"service": "workflow-service", "status": "ready", "activeWorkflows": len(active_tasks)}


@app.post("/internal/v1/runs/{run_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def start_workflow(run_id: str) -> Dict[str, str]:
    workflow_id = f"workflow_{run_id}"
    existing = active_tasks.get(workflow_id)
    if existing and not existing.done():
        return {"workflowId": workflow_id, "runId": run_id}
    active_tasks[workflow_id] = asyncio.create_task(execute_workflow(workflow_id, run_id))
    return {"workflowId": workflow_id, "runId": run_id}


@app.post("/internal/v1/runs/{run_id}/cancel")
async def cancel_workflow(run_id: str) -> Dict[str, str]:
    workflow_id = f"workflow_{run_id}"
    if task := active_tasks.get(workflow_id):
        task.cancel()
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/cancel")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="execution run not found")
    return {"workflowId": workflow_id, "runId": run_id, "status": "canceling"}
