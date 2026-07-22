import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Set
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
import psycopg
import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

app = FastAPI(title="execution-service", version="0.3.0")
instrument_app(app, "execution-service", ["plan", "run", "lease"])

Channel = Literal["ui", "api", "state", "mobile", "external", "quality"]
TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")
SENSITIVE_PARTS = {"authorization", "cookie", "password", "secret", "token", "api_key", "apikey", "access_token", "refresh_token"}
EXECUTOR_URLS = {"ui": os.getenv("OPENKATE_EXECUTOR_UI_URL", "http://127.0.0.1:8011"), "api": os.getenv("OPENKATE_EXECUTOR_API_URL", "http://127.0.0.1:8012"), "state": os.getenv("OPENKATE_EXECUTOR_STATE_URL", "http://127.0.0.1:8013")}


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ExecutionStep(ApiModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")
    channel: Channel
    action: str = Field(min_length=1, max_length=100)
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    input: Dict[str, Any] = Field(default_factory=dict)
    save: Dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = Field(default=10000, alias="timeoutMs", ge=100, le=300000)
    idempotent: bool = True
    compensation: Optional[str] = Field(default=None, max_length=100)


class PlanCreate(ApiModel):
    scenario_version: int = Field(alias="scenarioVersion", ge=1)
    scenario_status: str = Field(alias="scenarioStatus")
    steps: List[ExecutionStep] = Field(min_length=1)
    variables: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=300000, alias="timeoutMs", ge=1000, le=3600000)


class PlanUpdate(ApiModel):
    steps: Optional[List[ExecutionStep]] = Field(default=None, min_length=1)
    variables: Optional[Dict[str, Any]] = None
    timeout_ms: Optional[int] = Field(default=None, alias="timeoutMs", ge=1000, le=3600000)


class RunCreate(ApiModel):
    plan_id: str = Field(alias="planId", min_length=1)
    environment_id: str = Field(alias="environmentId", min_length=1)
    variables: Dict[str, Any] = Field(default_factory=dict)
    allowed_hosts: List[str] = Field(default_factory=list, alias="allowedHosts")
    account_refs: List[str] = Field(default_factory=list, alias="accountRefs")
    data_set_refs: List[str] = Field(default_factory=list, alias="dataSetRefs")


class StepComplete(ApiModel):
    output: Dict[str, Any] = Field(default_factory=dict)
    assertions: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list, alias="evidenceRefs")


class StepFail(ApiModel):
    category: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class ExecutionStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.plans: Dict[str, Dict[str, Any]] = {}
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.leases: Dict[str, Dict[str, Any]] = {}
        self.idempotency: Dict[str, str] = {}
        self.events: List[Dict[str, Any]] = []

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def event(self, event_type: str, plan: Dict[str, Any]) -> None:
        self._record_event(event_type, plan["projectId"], plan["id"], {"executionPlan": deepcopy(plan)})

    def run_event(self, event_type: str, run: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> None:
        self._record_event(event_type, run["projectId"], run["id"], payload or {"runId": run["id"], "status": run["status"]})

    def _record_event(self, event_type: str, project_id: str, aggregate_id: str, payload: Dict[str, Any]) -> None:
        event = {"eventId": str(uuid4()), "eventType": event_type, "projectId": project_id, "aggregateId": aggregate_id, "occurredAt": self.now(), "payload": payload}
        self.events.append(event)
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO execution_schema.outbox_events (id, aggregate_id, event_type, payload, occurred_at) VALUES (%s, %s, %s, %s, %s)", (event["eventId"], aggregate_id, event_type, Jsonb(event), event["occurredAt"]))

    def save_plan(self, plan: Dict[str, Any]) -> None:
        self.plans[plan["id"]] = deepcopy(plan)
        if not self.database_url:
            return
        with psycopg.connect(self.database_url) as connection:
            connection.execute("INSERT INTO execution_schema.execution_plans (id, project_id, scenario_id, scenario_version, status, version, revision, variables, timeout_ms, created_at, updated_at, document) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, version = EXCLUDED.version, revision = EXCLUDED.revision, variables = EXCLUDED.variables, timeout_ms = EXCLUDED.timeout_ms, updated_at = EXCLUDED.updated_at, document = EXCLUDED.document", (plan["id"], plan["projectId"], plan["scenarioId"], plan["scenarioVersion"], plan["status"], plan["version"], plan["revision"], Jsonb(plan["variables"]), plan["timeoutMs"], plan["createdAt"], plan["updatedAt"], Jsonb(plan)))
            connection.execute("DELETE FROM execution_schema.execution_steps WHERE plan_id = %s", (plan["id"],))
            for position, step_id in enumerate(plan["orderedStepIds"]):
                step = next(item for item in plan["steps"] if item["id"] == step_id)
                connection.execute("INSERT INTO execution_schema.execution_steps (plan_id, id, channel, action, position, depends_on, input, save, timeout_ms, idempotent, compensation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (plan["id"], step["id"], step["channel"], step["action"], position, step["dependsOn"], Jsonb(step["input"]), Jsonb(step["save"]), step["timeoutMs"], step["idempotent"], step.get("compensation")))

    def save_run(self, run: Dict[str, Any]) -> None:
        self.runs[run["id"]] = deepcopy(run)
        if not self.database_url:
            return
        lease = self.leases[run["leaseId"]]
        with psycopg.connect(self.database_url) as connection:
            connection.execute("INSERT INTO execution_schema.execution_runs (id, project_id, scenario_id, scenario_version, plan_id, environment_id, status, attempt, retry_of, deadline, created_at, started_at, completed_at, document) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, completed_at = EXCLUDED.completed_at, document = EXCLUDED.document", (run["id"], run["projectId"], run["scenarioId"], run["scenarioVersion"], run["planId"], run["environmentId"], run["status"], run["attempt"], run["retryOf"], run["deadline"], run["createdAt"], run["startedAt"], run["completedAt"], Jsonb(run)))
            connection.execute("INSERT INTO execution_schema.resource_leases (id, run_id, account_ref, data_set_ref, browser_context_id, status, acquired_at, released_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, released_at = EXCLUDED.released_at", (lease["id"], run["id"], lease["accountLeaseId"], lease["dataSetId"], lease["browserContextId"], lease["status"], lease["acquiredAt"], lease["releasedAt"]))
            connection.execute("DELETE FROM execution_schema.step_results WHERE run_id = %s", (run["id"],))
            connection.execute("DELETE FROM execution_schema.run_variables WHERE run_id = %s", (run["id"],))
            for result in run["stepResults"]:
                connection.execute("INSERT INTO execution_schema.step_results (run_id, step_id, status, output_summary, assertions, evidence_refs, error, started_at, completed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (run["id"], result["stepId"], result["status"], Jsonb(result["outputSummary"]), Jsonb(result["assertions"]), result["evidenceRefs"], Jsonb(result["error"]) if result["error"] else None, result["startedAt"], result["completedAt"]))
            for name, value in run["_variables"].items():
                connection.execute("INSERT INTO execution_schema.run_variables (run_id, name, value, sensitive) VALUES (%s, %s, %s, %s)", (run["id"], name, Jsonb(value), name in run["_sensitiveVariables"]))

    def plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        if plan_id in self.plans or not self.database_url:
            return self.plans.get(plan_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT document FROM execution_schema.execution_plans WHERE id = %s", (plan_id,)).fetchone()
        if row:
            self.plans[plan_id] = row["document"]
        return self.plans.get(plan_id)

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        if run_id in self.runs or not self.database_url:
            return self.runs.get(run_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT document FROM execution_schema.execution_runs WHERE id = %s", (run_id,)).fetchone()
            lease = connection.execute("SELECT id, account_ref, data_set_ref, browser_context_id, status, acquired_at, released_at FROM execution_schema.resource_leases WHERE run_id = %s", (run_id,)).fetchone()
        if row:
            self.runs[run_id] = row["document"]
            if lease:
                self.leases[lease["id"]] = {"id": lease["id"], "runId": run_id, "accountLeaseId": lease["account_ref"], "dataSetId": lease["data_set_ref"], "browserContextId": lease["browser_context_id"], "status": lease["status"], "acquiredAt": lease["acquired_at"].isoformat(), "releasedAt": lease["released_at"].isoformat() if lease["released_at"] else None}
        return self.runs.get(run_id)

    def idempotent_run(self, key: str) -> Optional[str]:
        if key in self.idempotency or not self.database_url:
            return self.idempotency.get(key)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT run_id FROM execution_schema.idempotency_keys WHERE key = %s", (key,)).fetchone()
        if row:
            self.idempotency[key] = row["run_id"]
        return self.idempotency.get(key)

    def remember_idempotency(self, key: str, run_id: str) -> None:
        self.idempotency[key] = run_id
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO execution_schema.idempotency_keys (key, run_id) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, run_id))

    def events_for(self, aggregate_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.database_url:
            return [event for event in self.events if aggregate_id is None or event["aggregateId"] == aggregate_id]
        query = "SELECT payload FROM execution_schema.outbox_events"
        params: tuple = ()
        if aggregate_id:
            query += " WHERE aggregate_id = %s"
            params = (aggregate_id,)
        query += " ORDER BY occurred_at, id"
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(query, params).fetchall()
        return [row["payload"] for row in rows]


store = ExecutionStore(os.getenv("OPENKATE_EXECUTION_DATABASE_URL"))


def template_variables(value: Any) -> Set[str]:
    if isinstance(value, str):
        return set(TEMPLATE_PATTERN.findall(value))
    if isinstance(value, list):
        return set().union(*(template_variables(item) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(template_variables(item) for item in value.values())) if value else set()
    return set()


def validate_dag(steps: List[ExecutionStep], initial_variables: Set[str]) -> List[str]:
    by_id = {step.id: step for step in steps}
    if len(by_id) != len(steps):
        raise ValueError("step ids must be unique")

    for step in steps:
        missing = set(step.depends_on) - set(by_id)
        if missing:
            raise ValueError(f"step {step.id} depends on unknown steps: {', '.join(sorted(missing))}")
        if step.id in step.depends_on:
            raise ValueError(f"step {step.id} cannot depend on itself")

    dependents: Dict[str, List[str]] = {step.id: [] for step in steps}
    indegree = {step.id: len(set(step.depends_on)) for step in steps}
    for step in steps:
        for dependency in set(step.depends_on):
            dependents[dependency].append(step.id)

    ready = [step.id for step in steps if indegree[step.id] == 0]
    ordered: List[str] = []
    while ready:
        step_id = ready.pop(0)
        ordered.append(step_id)
        for dependent in dependents[step_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(steps):
        raise ValueError("execution plan contains a dependency cycle")

    produced_by: Dict[str, str] = {}
    available_after: Dict[str, Set[str]] = {}
    for step_id in ordered:
        step = by_id[step_id]
        available = set(initial_variables)
        for dependency in step.depends_on:
            available.update(available_after[dependency])
        missing_variables = template_variables(step.input) - available
        if missing_variables:
            raise ValueError(f"step {step.id} references unavailable variables: {', '.join(sorted(missing_variables))}")
        for variable in step.save.values():
            if variable in initial_variables or variable in produced_by:
                raise ValueError(f"variable {variable} has more than one producer")
            produced_by[variable] = step.id
            available.add(variable)
        available_after[step.id] = available
    return ordered


def get_plan(plan_id: str) -> Dict[str, Any]:
    plan = store.plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="execution plan not found")
    return plan


def get_run(run_id: str) -> Dict[str, Any]:
    run = store.run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execution run not found")
    return run


def public_run(run: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(run)
    result.pop("_variables", None)
    result.pop("_sensitiveVariables", None)
    return result


def sensitive_variable(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return normalized in SENSITIVE_PARTS or any(normalized.endswith(f"_{part}") for part in SENSITIVE_PARTS)


def release_lease(run: Dict[str, Any]) -> None:
    lease = store.leases[run["leaseId"]]
    if lease["status"] == "active":
        lease["status"] = "released"
        lease["releasedAt"] = store.now()


def available_resource(field: str, configured: List[str], prefix: str) -> str:
    if not configured:
        return f"{prefix}_{uuid4().hex[:12]}"
    active = {lease[field] for lease in store.leases.values() if lease["status"] == "active"}
    for reference in configured:
        if reference not in active:
            return reference
    raise HTTPException(status_code=409, detail=f"no {prefix} resource is available")


def create_run_record(
    plan: Dict[str, Any],
    environment_id: str,
    variables: Dict[str, Any],
    allowed_hosts: Optional[List[str]] = None,
    account_refs: Optional[List[str]] = None,
    data_set_refs: Optional[List[str]] = None,
    retry_of: Optional[str] = None,
) -> Dict[str, Any]:
    now = store.now()
    run_id = f"run_{uuid4().hex[:12]}"
    lease_id = f"lease_{uuid4().hex[:12]}"
    account_pool = account_refs or []
    data_set_pool = data_set_refs or []
    lease = {
        "id": lease_id,
        "runId": run_id,
        "accountLeaseId": available_resource("accountLeaseId", account_pool, "account"),
        "dataSetId": available_resource("dataSetId", data_set_pool, "dataset"),
        "browserContextId": f"browser_{run_id}",
        "status": "active",
        "acquiredAt": now,
        "releasedAt": None,
    }
    run = {
        "id": run_id,
        "projectId": plan["projectId"],
        "scenarioId": plan["scenarioId"],
        "scenarioVersion": plan["scenarioVersion"],
        "planId": plan["id"],
        "environmentId": environment_id,
        "allowedHosts": list(allowed_hosts or []),
        "accountPool": list(account_pool),
        "dataSetPool": list(data_set_pool),
        "status": "running",
        "leaseId": lease_id,
        "retryOf": retry_of,
        "attempt": 1 if retry_of is None else store.runs[retry_of]["attempt"] + 1,
        "variables": sorted({**plan["variables"], **variables}),
        "_variables": {**deepcopy(plan["variables"]), **deepcopy(variables)},
        "_sensitiveVariables": sorted(name for name in {**plan["variables"], **variables} if sensitive_variable(name)),
        "stepResults": [
            {"stepId": step_id, "status": "pending", "startedAt": None, "completedAt": None, "outputSummary": {}, "assertions": [], "evidenceRefs": [], "error": None}
            for step_id in plan["orderedStepIds"]
        ],
        "createdAt": now,
        "startedAt": now,
        "completedAt": None,
        "deadline": (datetime.now(timezone.utc) + timedelta(milliseconds=plan["timeoutMs"])).isoformat(),
    }
    store.leases[lease_id] = lease
    store.runs[run_id] = run
    store.run_event("execution.run.requested.v1", run)
    store.run_event("execution.run.started.v1", run)
    return run


def result_for(run: Dict[str, Any], step_id: str) -> Dict[str, Any]:
    for result in run["stepResults"]:
        if result["stepId"] == step_id:
            return result
    raise HTTPException(status_code=404, detail="execution step not found")


def require_active_deadline(run: Dict[str, Any]) -> None:
    if datetime.now(timezone.utc) < datetime.fromisoformat(run["deadline"]):
        return
    run["status"] = "failed"
    run["completedAt"] = store.now()
    for result in run["stepResults"]:
        if result["status"] in {"pending", "running"}:
            result.update({"status": "failed", "completedAt": store.now(), "error": {"category": "timeout", "message": "execution plan deadline exceeded"}})
    release_lease(run)
    store.run_event("execution.run.completed.v1", run, {"runId": run["id"], "status": "failed", "category": "timeout"})
    store.save_run(run)
    raise HTTPException(status_code=408, detail="execution plan deadline exceeded")


def value_at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise HTTPException(status_code=422, detail=f"step output does not contain {path}")
        current = current[part]
    return current


def require_match(plan: Dict[str, Any], if_match: Optional[str]) -> None:
    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match is required")
    if if_match.removeprefix("W/").strip('"') != str(plan["revision"]):
        raise HTTPException(status_code=409, detail={"code": "EXECUTION_PLAN_CONFLICT", "message": "execution plan has been updated"})


def with_etag(response: Response, plan: Dict[str, Any]) -> Dict[str, Any]:
    response.headers["ETag"] = f'"{plan["revision"]}"'
    return deepcopy(plan)


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "execution-service", "status": "ready"}


@app.get("/internal/v1/projects/{project_id}/executor-capabilities")
async def executor_capabilities(project_id: str) -> Dict[str, Any]:
    items = []
    async with httpx.AsyncClient(timeout=1.0) as client:
        for channel, url in EXECUTOR_URLS.items():
            try:
                response = await client.get(f"{url}/health")
                body = response.json() if response.is_success else {}
                items.append({"channel": channel, "worker": body.get("worker"), "capabilities": body.get("capabilities", []), "status": "ready" if response.is_success else "unavailable"})
            except httpx.HTTPError:
                items.append({"channel": channel, "worker": None, "capabilities": [], "status": "unavailable"})
    return {"projectId": project_id, "items": items}


@app.post("/internal/v1/scenarios/{scenario_id}/execution-plans", status_code=status.HTTP_201_CREATED)
async def create_execution_plan(scenario_id: str, payload: PlanCreate, response: Response, x_project_id: str = Header(alias="X-OpenKATE-Project-Id")) -> Dict[str, Any]:
    if payload.scenario_status != "approved":
        raise HTTPException(status_code=409, detail="only approved scenarios can create execution plans")
    try:
        ordered_step_ids = validate_dag(payload.steps, set(payload.variables))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    now = store.now()
    plan = {
        "id": f"plan_{uuid4().hex[:12]}",
        "projectId": x_project_id,
        "scenarioId": scenario_id,
        "scenarioVersion": payload.scenario_version,
        "status": "draft",
        "version": 1,
        "revision": 1,
        "steps": [step.model_dump(by_alias=True) for step in payload.steps],
        "orderedStepIds": ordered_step_ids,
        "variables": deepcopy(payload.variables),
        "timeoutMs": payload.timeout_ms,
        "createdAt": now,
        "updatedAt": now,
    }
    store.save_plan(plan)
    store.event("execution.plan.created.v1", plan)
    return with_etag(response, plan)


@app.get("/internal/v1/execution-plans/{plan_id}")
async def execution_plan_detail(plan_id: str, response: Response) -> Dict[str, Any]:
    return with_etag(response, get_plan(plan_id))


@app.patch("/internal/v1/execution-plans/{plan_id}")
async def update_execution_plan(plan_id: str, payload: PlanUpdate, response: Response, if_match: Optional[str] = Header(default=None, alias="If-Match")) -> Dict[str, Any]:
    plan = get_plan(plan_id)
    require_match(plan, if_match)
    steps = payload.steps or [ExecutionStep.model_validate(step) for step in plan["steps"]]
    variables = payload.variables if payload.variables is not None else plan["variables"]
    try:
        ordered_step_ids = validate_dag(steps, set(variables))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if payload.steps is not None:
        plan["steps"] = [step.model_dump(by_alias=True) for step in steps]
        plan["orderedStepIds"] = ordered_step_ids
    if payload.variables is not None:
        plan["variables"] = deepcopy(payload.variables)
    if payload.timeout_ms is not None:
        plan["timeoutMs"] = payload.timeout_ms
    plan["version"] += 1
    plan["revision"] += 1
    plan["updatedAt"] = store.now()
    store.save_plan(plan)
    return with_etag(response, plan)


@app.get("/internal/v1/events")
async def list_events() -> List[Dict[str, Any]]:
    return deepcopy(store.events_for())


@app.post("/internal/v1/scenarios/{scenario_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    scenario_id: str,
    payload: RunCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_project_id: str = Header(alias="X-OpenKATE-Project-Id"),
) -> Dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(status_code=428, detail="Idempotency-Key is required")
    key = f"{x_project_id}:{idempotency_key}"
    if existing_id := store.idempotent_run(key):
        return public_run(get_run(existing_id))
    plan = get_plan(payload.plan_id)
    if plan["projectId"] != x_project_id or plan["scenarioId"] != scenario_id:
        raise HTTPException(status_code=404, detail="execution plan not found for scenario")
    run = create_run_record(plan, payload.environment_id, payload.variables, payload.allowed_hosts, payload.account_refs, payload.data_set_refs)
    store.save_run(run)
    store.remember_idempotency(key, run["id"])
    return public_run(run)


@app.get("/internal/v1/runs/{run_id}")
async def run_detail(run_id: str) -> Dict[str, Any]:
    return public_run(get_run(run_id))


@app.get("/internal/v1/runs/{run_id}/context", include_in_schema=False)
async def run_context(run_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    return {"run": deepcopy(run), "plan": deepcopy(get_plan(run["planId"]))}


@app.get("/internal/v1/runs/{run_id}/events")
async def run_events(run_id: str, after: int = 0) -> Dict[str, Any]:
    get_run(run_id)
    events = store.events_for(run_id)
    return {"events": deepcopy(events[after:]), "next": len(events)}


@app.post("/internal/v1/runs/{run_id}/steps/{step_id}/start")
async def start_step(run_id: str, step_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail="run is not active")
    require_active_deadline(run)
    result = result_for(run, step_id)
    if result["status"] != "pending":
        raise HTTPException(status_code=409, detail="step is not pending")
    plan = get_plan(run["planId"])
    step = next(item for item in plan["steps"] if item["id"] == step_id)
    unfinished = [dependency for dependency in step["dependsOn"] if result_for(run, dependency)["status"] != "completed"]
    if unfinished:
        raise HTTPException(status_code=409, detail=f"step dependencies are incomplete: {', '.join(unfinished)}")
    result["status"] = "running"
    result["startedAt"] = store.now()
    store.run_event("execution.step.started.v1", run, {"runId": run_id, "stepId": step_id, "channel": step["channel"]})
    store.save_run(run)
    return deepcopy(result)


@app.post("/internal/v1/runs/{run_id}/steps/{step_id}/complete")
async def complete_step(run_id: str, step_id: str, payload: StepComplete) -> Dict[str, Any]:
    run = get_run(run_id)
    result = result_for(run, step_id)
    if run["status"] != "running" or result["status"] != "running":
        raise HTTPException(status_code=409, detail="step is not running")
    plan = get_plan(run["planId"])
    step = next(item for item in plan["steps"] if item["id"] == step_id)
    for output_path, variable in step["save"].items():
        run["_variables"][variable] = value_at_path(payload.output, output_path)
        if sensitive_variable(variable) and variable not in run["_sensitiveVariables"]:
            run["_sensitiveVariables"].append(variable)
            run["_sensitiveVariables"].sort()
        if variable not in run["variables"]:
            run["variables"].append(variable)
            run["variables"].sort()
    result.update(
        {
            "status": "completed",
            "completedAt": store.now(),
            "outputSummary": {"keys": sorted(payload.output)},
            "assertions": deepcopy(payload.assertions),
            "evidenceRefs": list(payload.evidence_refs),
        }
    )
    store.run_event("execution.step.completed.v1", run, {"runId": run_id, "stepId": step_id, "channel": step["channel"], "status": "completed"})
    if all(item["status"] == "completed" for item in run["stepResults"]):
        run["status"] = "completed"
        run["completedAt"] = store.now()
        release_lease(run)
        store.run_event("execution.run.completed.v1", run)
    store.save_run(run)
    return deepcopy(result)


@app.post("/internal/v1/runs/{run_id}/steps/{step_id}/fail")
async def fail_step(run_id: str, step_id: str, payload: StepFail) -> Dict[str, Any]:
    run = get_run(run_id)
    result = result_for(run, step_id)
    if run["status"] != "running" or result["status"] not in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="step cannot fail in its current state")
    result.update({"status": "failed", "completedAt": store.now(), "error": {"category": payload.category, "message": payload.message}})
    run["status"] = "failed"
    run["completedAt"] = store.now()
    release_lease(run)
    store.run_event("execution.step.failed.v1", run, {"runId": run_id, "stepId": step_id, "status": "failed", "category": payload.category})
    store.run_event("execution.run.completed.v1", run)
    store.save_run(run)
    return deepcopy(result)


@app.post("/internal/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if run["status"] == "canceled":
        return public_run(run)
    if run["status"] in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="completed run cannot be canceled")
    run["status"] = "canceled"
    run["completedAt"] = store.now()
    for result in run["stepResults"]:
        if result["status"] in {"pending", "running"}:
            result["status"] = "canceled"
            result["completedAt"] = store.now()
    release_lease(run)
    store.run_event("execution.run.canceled.v1", run)
    store.save_run(run)
    return public_run(run)


@app.post("/internal/v1/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(run_id: str, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
    previous = get_run(run_id)
    if previous["status"] not in {"failed", "canceled"}:
        raise HTTPException(status_code=409, detail="only failed or canceled runs can be retried")
    if not idempotency_key:
        raise HTTPException(status_code=428, detail="Idempotency-Key is required")
    key = f"{previous['projectId']}:{idempotency_key}"
    if existing_id := store.idempotent_run(key):
        return public_run(get_run(existing_id))
    retried = create_run_record(
        get_plan(previous["planId"]),
        previous["environmentId"],
        {},
        previous["allowedHosts"],
        previous["accountPool"],
        previous["dataSetPool"],
        retry_of=run_id,
    )
    store.save_run(retried)
    store.remember_idempotency(key, retried["id"])
    return public_run(retried)
