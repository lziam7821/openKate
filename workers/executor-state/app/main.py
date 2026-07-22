import json
import os
import re
import time
import asyncio
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException

from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, ExecutorRuntime, assert_allowed_url, evaluate_assertions, redact, render_templates, store_evidence
from openkate_common.service_app import instrument_app

app = FastAPI(title="executor-state", version="0.3.0")
instrument_app(app, "executor-state", ["state.postgresql.read-only"])

READ_ONLY_SQL = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def secret_value(reference: str) -> str:
    key = "OPENKATE_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", reference).upper()
    value = os.getenv(key)
    if not value:
        raise HTTPException(status_code=422, detail=f"secret reference is unavailable: {reference}")
    return value


def execute_state(request: ExecutorRequest, connection_factory: Optional[Callable[..., Any]] = None, sleep: Callable[[float], None] = time.sleep, http_transport: Optional[httpx.BaseTransport] = None, cache_factory: Optional[Callable[[str], Any]] = None, message_fetcher: Optional[Callable[[str, str, float], Any]] = None) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    if request.action == "message":
        subject = str(payload.get("subject", ""))
        if not subject:
            raise HTTPException(status_code=422, detail="message subject is required")
        url = secret_value(str(payload.get("connectionSecretRef", "")))
        if message_fetcher is None:
            async def fetch(server: str, topic: str, timeout: float) -> Any:
                try:
                    import nats
                except ImportError as error:
                    raise HTTPException(status_code=503, detail="NATS executor dependency is unavailable") from error
                connection = await nats.connect(servers=[server])
                try:
                    subscription = await connection.subscribe(topic)
                    return await subscription.next_msg(timeout=timeout)
                finally:
                    await connection.drain()

            message_fetcher = fetch
        message = message_fetcher(url, subject, request.timeout_ms / 1000)
        if asyncio.iscoroutine(message):
            message = asyncio.run(message)
        body = message.data if hasattr(message, "data") else message
        if isinstance(body, bytes):
            try:
                body = json.loads(body)
            except ValueError:
                body = body.decode(errors="replace")
        actual = {"subject": subject, "body": body}
        assertions = evaluate_assertions(actual, payload.get("assertions", []))
        if any(not item["passed"] for item in assertions):
            raise HTTPException(status_code=422, detail="message assertion failed")
        return ExecutorResult(status="completed", output=actual, inputSummary=redact({"subject": subject, "connectionSecretRef": payload.get("connectionSecretRef")}), outputSummary=redact(actual), assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, "message", json.dumps(redact(actual)).encode(), "application/json")], environment={"executor": "state.nats"})
    if request.action == "cache":
        key = str(payload.get("key", ""))
        if not key:
            raise HTTPException(status_code=422, detail="cache key is required")
        if cache_factory is None:
            try:
                import redis
            except ImportError as error:
                raise HTTPException(status_code=503, detail="Redis executor dependency is unavailable") from error
            cache_factory = redis.from_url
        client = cache_factory(secret_value(str(payload.get("connectionSecretRef", ""))))
        value, ttl = client.get(key), client.ttl(key)
        if isinstance(value, bytes):
            value = value.decode()
        actual = {"key": key, "value": value, "ttl": ttl}
        assertions = evaluate_assertions(actual, payload.get("assertions", []))
        if any(not item["passed"] for item in assertions):
            raise HTTPException(status_code=422, detail="cache assertion failed")
        return ExecutorResult(status="completed", output=actual, inputSummary=redact({"key": key, "connectionSecretRef": payload.get("connectionSecretRef")}), outputSummary=redact(actual), assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, "cache", json.dumps(redact(actual)).encode(), "application/json")], environment={"executor": "state.redis.read_only"})
    if request.action in {"log", "trace"}:
        url = str(payload.get("url", ""))
        assert_allowed_url(url, request.allowed_hosts)
        with httpx.Client(transport=http_transport, timeout=request.timeout_ms / 1000) as client:
            response = client.get(url, params=payload.get("params", {}), headers=payload.get("headers", {}))
        try:
            actual: Any = response.json()
        except ValueError:
            actual = {"text": response.text}
        assertions = evaluate_assertions(actual, payload.get("assertions", []))
        if response.is_error or any(not item["passed"] for item in assertions):
            raise HTTPException(status_code=422, detail=f"{request.action} assertion failed")
        return ExecutorResult(status="completed", output=actual, inputSummary=redact({"url": url, "params": payload.get("params", {}), "headers": payload.get("headers", {})}), outputSummary=redact(actual), assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, request.action, json.dumps(redact(actual)).encode(), "application/json")], environment={"executor": f"state.{request.action}"})
    query = str(payload.get("query", ""))
    if not READ_ONLY_SQL.match(query) or ";" in query.rstrip().rstrip(";"):
        raise HTTPException(status_code=422, detail="state executor only permits one SELECT or WITH query")
    if connection_factory is None:
        try:
            import psycopg
        except ImportError as error:
            raise HTTPException(status_code=503, detail="PostgreSQL executor dependency is unavailable") from error
        connection_factory = psycopg.connect
    dsn = secret_value(str(payload.get("connectionSecretRef", "")))
    wait = request.action == "wait"
    interval = int(payload.get("pollIntervalMs", 250))
    multiplier = float(payload.get("backoffMultiplier", 1))
    if wait and (interval < 1 or multiplier < 1):
        raise HTTPException(status_code=422, detail="poll interval and backoff multiplier must be positive")
    deadline = time.monotonic() + request.timeout_ms / 1000
    while True:
        with connection_factory(dsn, options="-c default_transaction_read_only=on") as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, payload.get("params", {}))
                columns = [column.name for column in cursor.description or []]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        actual = {"rows": rows, "rowCount": len(rows)}
        assertions = evaluate_assertions(actual, payload.get("assertions", []))
        if not any(not assertion["passed"] for assertion in assertions):
            break
        if not wait or time.monotonic() >= deadline:
            raise HTTPException(status_code=422, detail="state assertion timed out" if wait else "state assertion failed")
        sleep(min(interval / 1000, max(0, deadline - time.monotonic())))
        interval = int(interval * multiplier)
    return ExecutorResult(
        status="completed",
        output=actual,
        inputSummary=redact({"query": query, "params": payload.get("params", {}), "connectionSecretRef": payload.get("connectionSecretRef")}),
        outputSummary=redact(actual),
        assertions=assertions,
        evidenceRefs=[store_evidence(request.run_id, request.step_id, "query", json.dumps(redact(actual)).encode(), "application/json")],
        environment={"executor": "state.postgresql.read_only", "polling": wait},
    )


executor = ExecutorRuntime(["state.postgresql.read_only", "state.redis.read_only", "state.nats", "state.eventual-consistency", "state.log", "state.trace"], lambda request: asyncio.to_thread(execute_state, request))


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-state", "status": "ready", "capabilities": ["state.postgresql.read_only", "state.redis.read_only", "state.nats", "state.eventual-consistency", "state.log", "state.trace"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await executor.execute(request)


@app.post("/cancel")
async def cancel(request: ExecutorRequest) -> Dict[str, str]:
    await executor.cancel(request)
    return {"runId": request.run_id, "stepId": request.step_id, "status": "canceling"}
