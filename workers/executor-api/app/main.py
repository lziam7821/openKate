import json
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException

from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, assert_allowed_url, evaluate_assertions, redact, render_templates, store_evidence
from openkate_common.service_app import instrument_app

app = FastAPI(title="executor-api", version="0.3.0")
instrument_app(app, "executor-api", ["api.http"])


async def execute_api(request: ExecutorRequest, transport: Optional[httpx.AsyncBaseTransport] = None) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    url = str(payload.get("url", ""))
    assert_allowed_url(url, request.allowed_hosts)
    method = str(payload.get("method", "GET")).upper()
    headers = payload.get("headers", {})
    async with httpx.AsyncClient(transport=transport, timeout=request.timeout_ms / 1000) as client:
        response = await client.request(method, url, headers=headers, json=payload.get("json"), params=payload.get("params"))
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    actual = {"statusCode": response.status_code, "body": body, "headers": dict(response.headers)}
    assertions = evaluate_assertions(actual, payload.get("assertions", []))
    if any(not assertion["passed"] for assertion in assertions):
        raise HTTPException(status_code=422, detail="API assertion failed")
    return ExecutorResult(
        status="completed",
        output=actual,
        inputSummary=redact({"method": method, "url": url, "headers": headers, "json": payload.get("json")}),
        outputSummary=redact(actual),
        assertions=assertions,
        evidenceRefs=[store_evidence(request.run_id, request.step_id, "http", json.dumps({"request": redact({"method": method, "url": url, "headers": headers, "json": payload.get("json")}), "response": redact(actual)}).encode(), "application/json")],
        environment={"executor": "api.http", "httpVersion": response.http_version},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-api", "status": "ready", "capabilities": ["api.http"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await execute_api(request)
