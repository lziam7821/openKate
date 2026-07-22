import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import psycopg
from fastapi import FastAPI, HTTPException, Request, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from openkate_common.service_app import instrument_app
from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, ExecutorRuntime, evaluate_assertions, redact, render_templates, store_evidence

app = FastAPI(title="executor-external", version="0.8.0")
instrument_app(app, "executor-external", ["external.callback", "external.test-data"])


class CallbackStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.callbacks: Dict[str, List[Dict[str, Any]]] = {}

    def receive(self, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        callback = {"id": f"callback_{uuid4().hex[:12]}", "token": token, "payload": deepcopy(payload), "receivedAt": datetime.now(timezone.utc).isoformat()}
        self.callbacks.setdefault(token, []).append(callback)
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO execution_schema.external_callbacks (id, callback_token, payload, received_at) VALUES (%s, %s, %s, %s)", (callback["id"], token, Jsonb(payload), callback["receivedAt"]))
        return callback

    def callbacks_for(self, token: str) -> List[Dict[str, Any]]:
        if self.database_url:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute("SELECT id, callback_token, payload, received_at FROM execution_schema.external_callbacks WHERE callback_token = %s ORDER BY received_at", (token,)).fetchall()
            return [{"id": row["id"], "token": row["callback_token"], "payload": row["payload"], "receivedAt": row["received_at"].isoformat()} for row in rows]
        return deepcopy(self.callbacks.get(token, []))


store = CallbackStore(os.getenv("OPENKATE_EXECUTION_DATABASE_URL"))


async def execute_external(request: ExecutorRequest) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    if request.action == "data":
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="test data object is required")
        output, assertions = data, []
    elif request.action == "waitForCallback":
        token = str(payload.get("callbackToken", ""))
        if not token:
            raise HTTPException(status_code=422, detail="callback token is required")
        deadline = asyncio.get_running_loop().time() + request.timeout_ms / 1000
        while True:
            callbacks = store.callbacks_for(token)
            output = {"callbacks": callbacks}
            assertions = evaluate_assertions(output, payload.get("assertions", []))
            if callbacks and not any(not item["passed"] for item in assertions):
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise HTTPException(status_code=422, detail="callback assertion timed out")
            await asyncio.sleep(min(float(payload.get("pollIntervalMs", 250)) / 1000, max(0, deadline - asyncio.get_running_loop().time())))
    else:
        raise HTTPException(status_code=422, detail=f"unsupported external action: {request.action}")
    return ExecutorResult(status="completed", output=output, inputSummary=redact(payload), outputSummary=redact(output), assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, "callback" if request.action == "waitForCallback" else "test-data", json.dumps(redact(output)).encode(), "application/json")], environment={"executor": "external.callback" if request.action == "waitForCallback" else "external.test-data"})


executor = ExecutorRuntime(["external.callback", "external.test-data"], execute_external)


@app.post("/callbacks/{token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_callback(token: str, request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="callback body must be JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="callback body must be an object")
    return store.receive(token, payload)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-external", "status": "ready", "capabilities": ["external.callback", "external.test-data"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await executor.execute(request)


@app.post("/cancel")
async def cancel(request: ExecutorRequest) -> Dict[str, str]:
    await executor.cancel(request)
    return {"runId": request.run_id, "stepId": request.step_id, "status": "canceling"}
