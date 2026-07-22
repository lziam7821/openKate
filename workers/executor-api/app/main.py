import asyncio
import base64
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException

from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, assert_allowed_url, evaluate_assertions, redact, render_templates, store_evidence
from openkate_common.service_app import instrument_app

app = FastAPI(title="executor-api", version="0.3.0")
instrument_app(app, "executor-api", ["api.http"])


async def execute_api(request: ExecutorRequest, transport: Optional[httpx.AsyncBaseTransport] = None, grpc_channel_factory: Optional[Any] = None) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    if request.action == "grpc":
        target = str(payload.get("target", ""))
        host = target.rsplit(":", 1)[0]
        method = str(payload.get("method", ""))
        if not target or host not in request.allowed_hosts or not method.startswith("/"):
            raise HTTPException(status_code=422, detail="gRPC target, allowlisted host, and absolute method are required")
        try:
            request_bytes = base64.b64decode(str(payload.get("requestBase64", "")), validate=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="gRPC requestBase64 is invalid") from error
        if grpc_channel_factory is None:
            try:
                import grpc
            except ImportError as error:
                raise HTTPException(status_code=503, detail="gRPC executor dependency is unavailable") from error
            grpc_channel_factory = grpc.insecure_channel

        def invoke() -> bytes:
            with grpc_channel_factory(target) as channel:
                return channel.unary_unary(method)(request_bytes, timeout=request.timeout_ms / 1000, metadata=list(payload.get("metadata", {}).items()))

        response_bytes = await asyncio.to_thread(invoke)
        actual = {"responseBase64": base64.b64encode(response_bytes).decode()}
        assertions = evaluate_assertions(actual, payload.get("assertions", []))
        if any(not assertion["passed"] for assertion in assertions):
            raise HTTPException(status_code=422, detail="gRPC assertion failed")
        return ExecutorResult(status="completed", output=actual, inputSummary=redact({"target": target, "method": method, "metadata": payload.get("metadata", {})}), outputSummary=actual, assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, "grpc", json.dumps(actual).encode(), "application/json")], environment={"executor": "api.grpc"})
    url = str(payload.get("url", ""))
    assert_allowed_url(url, request.allowed_hosts)
    graphql = request.action == "graphql"
    if graphql and not isinstance(payload.get("query"), str):
        raise HTTPException(status_code=422, detail="GraphQL query is required")
    method = "POST" if graphql else str(payload.get("method", "GET")).upper()
    headers = dict(payload.get("headers", {}))
    body = {"query": payload["query"], "variables": payload.get("variables", {}), "operationName": payload.get("operationName")} if graphql else payload.get("json")
    if graphql:
        headers.setdefault("Content-Type", "application/json")
    async with httpx.AsyncClient(transport=transport, timeout=request.timeout_ms / 1000) as client:
        response = await client.request(method, url, headers=headers, json=body, params=payload.get("params"))
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
        inputSummary=redact({"protocol": "graphql" if graphql else "http", "method": method, "url": url, "headers": headers, "json": body}),
        outputSummary=redact(actual),
        assertions=assertions,
        evidenceRefs=[store_evidence(request.run_id, request.step_id, "graphql" if graphql else "http", json.dumps({"request": redact({"method": method, "url": url, "headers": headers, "json": body}), "response": redact(actual)}).encode(), "application/json")],
        environment={"executor": "api.graphql" if graphql else "api.http", "httpVersion": response.http_version},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-api", "status": "ready", "capabilities": ["api.http", "api.graphql", "api.grpc.unary"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await execute_api(request)
