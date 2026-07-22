import os
from html import escape
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Set

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

app = FastAPI(title="report-service", version="0.2.0")
instrument_app(app, "report-service", ["read-model", "event-consumer"])

ScenarioStatus = Literal["draft", "in_review", "approved", "rejected", "archived", "deprecated"]
RiskLevel = Literal["low", "medium", "high", "critical"]


class Event(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId", min_length=1)
    event_type: str = Field(alias="eventType", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    aggregate_id: str = Field(alias="aggregateId", min_length=1)
    occurred_at: str = Field(alias="occurredAt", min_length=1)
    payload: Dict[str, Any]


class ReportStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.models: Dict[str, Dict[str, Any]] = {}
        self.consumed_event_ids: Set[str] = set()

    def consume(self, event: Event) -> bool:
        scenario = event.payload.get("scenario")
        if self.database_url is None:
            if event.event_id in self.consumed_event_ids:
                return False
            self.consumed_event_ids.add(event.event_id)
            if scenario:
                self.models[event.aggregate_id] = deepcopy(scenario)
            return True
        with psycopg.connect(self.database_url) as connection:
            inserted = connection.execute(
                "INSERT INTO report_schema.consumed_events (event_id, event_type, occurred_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id",
                (event.event_id, event.event_type, event.occurred_at),
            ).fetchone()
            if inserted is None:
                return False
            if scenario:
                connection.execute(
                    "INSERT INTO report_schema.scenario_read_models (scenario_id, project_id, status, risk_level, owner, tags, document, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (scenario_id) DO UPDATE SET project_id = EXCLUDED.project_id, status = EXCLUDED.status, risk_level = EXCLUDED.risk_level, owner = EXCLUDED.owner, tags = EXCLUDED.tags, document = EXCLUDED.document, updated_at = EXCLUDED.updated_at",
                    (event.aggregate_id, event.project_id, scenario["status"], scenario["riskLevel"], scenario["owner"], scenario.get("tags", []), Jsonb(scenario), scenario["updatedAt"]),
                )
        return True

    def list_models(self, project_id: str) -> List[Dict[str, Any]]:
        if self.database_url is None:
            return [item for item in self.models.values() if item.get("projectId") == project_id]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute("SELECT document FROM report_schema.scenario_read_models WHERE project_id = %s", (project_id,)).fetchall()
        return [deepcopy(row["document"]) for row in rows]

    def rebuild(self, events: List[Event]) -> int:
        if self.database_url is None:
            self.models.clear()
            self.consumed_event_ids.clear()
        else:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("TRUNCATE report_schema.scenario_read_models, report_schema.consumed_events")
        return sum(1 for event in events if self.consume(event))

    def ready(self) -> bool:
        if self.database_url is None:
            return True
        try:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("SELECT 1 FROM report_schema.scenario_read_models LIMIT 1")
            return True
        except psycopg.Error:
            return False


store = ReportStore(os.getenv("OPENKATE_REPORT_DATABASE_URL"))
EXECUTION_SERVICE_URL = os.getenv("OPENKATE_EXECUTION_SERVICE_URL", "http://127.0.0.1:8004")


def report_for(context: Dict[str, Any]) -> Dict[str, Any]:
    run, plan = context["run"], context["plan"]
    results = run["stepResults"]
    missing = [item["stepId"] for item in results if not item.get("evidenceRefs")]
    failures = [item for item in results if item["status"] == "failed"]
    if run["status"] == "completed" and not missing:
        outcome = "passed"
    elif missing:
        outcome = "inconclusive"
    elif any(item.get("error", {}).get("category") in {"environment", "executor_unavailable"} for item in failures):
        outcome = "blocked"
    elif failures or run["status"] in {"failed", "canceled"}:
        outcome = "failed"
    else:
        outcome = "inconclusive"
    evidences = [{"stepId": item["stepId"], "ref": ref} for item in results for ref in item.get("evidenceRefs", [])]
    return {"runId": run["id"], "scenarioId": run["scenarioId"], "scenarioVersion": run["scenarioVersion"], "planId": run["planId"], "outcome": outcome, "status": run["status"], "steps": [{"stepId": item["stepId"], "status": item["status"], "assertions": item.get("assertions", []), "evidenceRefs": item.get("evidenceRefs", []), "error": item.get("error"), "startedAt": item.get("startedAt"), "completedAt": item.get("completedAt")} for item in results], "evidences": evidences, "coverage": {"requiredSteps": len(plan["orderedStepIds"]), "coveredSteps": len(results) - len(missing), "missingEvidenceSteps": missing, "requiredEvidenceSatisfied": not missing}, "generatedAt": run.get("completedAt") or run["createdAt"]}


async def run_report(run_id: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{EXECUTION_SERVICE_URL}/internal/v1/runs/{run_id}/context")
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="execution service unavailable") from error
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="execution run not found")
    response.raise_for_status()
    return report_for(response.json())


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "report-service", "status": "ready" if store.ready() else "degraded"}


@app.post("/internal/v1/events")
async def consume_event(event: Event) -> Dict[str, bool]:
    return {"accepted": store.consume(event)}


@app.post("/internal/v1/read-model/rebuild")
async def rebuild_read_model(events: List[Event]) -> Dict[str, int]:
    return {"consumed": store.rebuild(events)}


@app.get("/internal/v1/projects/{project_id}/scenarios")
async def list_scenarios(
    project_id: str,
    q: Optional[str] = None,
    status: Optional[ScenarioStatus] = None,
    risk: Optional[RiskLevel] = None,
    tag: Optional[str] = None,
    owner: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
) -> Dict[str, Any]:
    items = store.list_models(project_id)
    if q:
        query = q.lower()
        items = [item for item in items if query in item.get("title", "").lower() or query in item.get("businessGoal", "").lower()]
    if status:
        items = [item for item in items if item.get("status") == status]
    if risk:
        items = [item for item in items if item.get("riskLevel") == risk]
    if tag:
        items = [item for item in items if tag in item.get("tags", [])]
    if owner:
        items = [item for item in items if item.get("owner") == owner]
    items.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
    start = (page - 1) * page_size
    return {"items": deepcopy(items[start : start + page_size]), "total": len(items), "page": page, "pageSize": page_size}


@app.get("/internal/v1/runs/{run_id}/report")
async def run_report_detail(run_id: str) -> Dict[str, Any]:
    return await run_report(run_id)


@app.get("/internal/v1/runs/{run_id}/evidences")
async def run_evidences(run_id: str) -> Dict[str, Any]:
    report = await run_report(run_id)
    return {"runId": run_id, "items": report["evidences"]}


@app.get("/internal/v1/runs/{run_id}/coverage")
async def run_coverage(run_id: str) -> Dict[str, Any]:
    report = await run_report(run_id)
    return {"runId": run_id, **report["coverage"]}


@app.get("/internal/v1/runs/{run_id}/compare/{other_run_id}")
async def compare_runs(run_id: str, other_run_id: str) -> Dict[str, Any]:
    current, other = await run_report(run_id), await run_report(other_run_id)
    by_step = {item["stepId"]: item for item in other["steps"]}
    return {"runId": run_id, "otherRunId": other_run_id, "outcome": {"from": other["outcome"], "to": current["outcome"]}, "steps": [{"stepId": item["stepId"], "from": by_step.get(item["stepId"], {}).get("status"), "to": item["status"], "evidenceDelta": len(item["evidenceRefs"]) - len(by_step.get(item["stepId"], {}).get("evidenceRefs", []))} for item in current["steps"]]}


@app.get("/internal/v1/runs/{run_id}/exports/{format}")
async def export_report(run_id: str, format: Literal["json", "html", "junit"]):
    report = await run_report(run_id)
    if format == "json":
        return report
    if format == "html":
        rows = "".join(f"<li>{escape(item['stepId'])}: {escape(item['status'])}</li>" for item in report["steps"])
        return PlainTextResponse(f"<html><body><h1>{escape(report['outcome'])}</h1><ul>{rows}</ul></body></html>", media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{run_id}.html"'})
    failures = "".join(f'<failure message="{escape((item.get("error") or {}).get("message", item["status"]))}" />' for item in report["steps"] if item["status"] != "completed")
    return PlainTextResponse(f'<testsuite name="openkate" tests="{len(report["steps"])}" failures="{len([item for item in report["steps"] if item["status"] != "completed"])}"><testcase name="{escape(run_id)}">{failures}</testcase></testsuite>', media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{run_id}.xml"'})
