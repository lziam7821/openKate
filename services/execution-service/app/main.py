import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="execution-service", version="0.3.0")

Channel = Literal["ui", "api", "state"]
TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")


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


class ExecutionStore:
    def __init__(self) -> None:
        self.plans: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def event(self, event_type: str, plan: Dict[str, Any]) -> None:
        self.events.append(
            {
                "eventId": str(uuid4()),
                "eventType": event_type,
                "projectId": plan["projectId"],
                "aggregateId": plan["id"],
                "occurredAt": self.now(),
                "payload": {"executionPlan": deepcopy(plan)},
            }
        )


store = ExecutionStore()


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
    plan = store.plans.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="execution plan not found")
    return plan


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
    store.plans[plan["id"]] = plan
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
    return with_etag(response, plan)


@app.get("/internal/v1/events")
async def list_events() -> List[Dict[str, Any]]:
    return deepcopy(store.events)
