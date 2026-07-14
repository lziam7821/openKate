import os
import re
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException

from openkate_executor import ExecutorRequest, ExecutorResult, evaluate_assertions, redact, render_templates
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


def execute_state(request: ExecutorRequest, connection_factory: Optional[Callable[..., Any]] = None) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
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
    with connection_factory(dsn, options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, payload.get("params", {}))
            columns = [column.name for column in cursor.description or []]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    actual = {"rows": rows, "rowCount": len(rows)}
    assertions = evaluate_assertions(actual, payload.get("assertions", []))
    if any(not assertion["passed"] for assertion in assertions):
        raise HTTPException(status_code=422, detail="state assertion failed")
    return ExecutorResult(
        status="completed",
        output=actual,
        inputSummary=redact({"query": query, "params": payload.get("params", {}), "connectionSecretRef": payload.get("connectionSecretRef")}),
        outputSummary=redact(actual),
        assertions=assertions,
        evidenceRefs=[f"run://{request.run_id}/steps/{request.step_id}/query"],
        environment={"executor": "state.postgresql.read_only"},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-state", "status": "ready", "capabilities": ["state.postgresql.read_only"]}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return execute_state(request)
