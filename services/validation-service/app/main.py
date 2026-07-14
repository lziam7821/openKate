from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="validation-service", version="0.2.0")

Role = Literal["owner", "maintainer", "reviewer", "developer", "viewer"]
ScenarioStatus = Literal["draft", "in_review", "approved", "rejected", "archived", "deprecated"]
RiskLevel = Literal["low", "medium", "high", "critical"]


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Risk(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    level: RiskLevel


class EvidenceAssertion(ApiModel):
    path: str = Field(min_length=1, max_length=300)
    operator: str = Field(min_length=1, max_length=100)
    expected: Optional[Any] = None
    expected_from: Optional[str] = Field(default=None, alias="expectedFrom")


class EvidencePoint(ApiModel):
    channel: Literal["ui", "api", "state"]
    target: str = Field(min_length=1, max_length=300)
    observation: str = Field(min_length=1, max_length=2000)
    assertions: List[EvidenceAssertion] = Field(min_length=1)
    required: bool = True


class ScenarioCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    business_goal: str = Field(alias="businessGoal", min_length=1, max_length=2000)
    actors: List[str] = Field(min_length=1)
    preconditions: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = Field(alias="riskLevel")
    invariants: List[str] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    evidence_points: List[EvidencePoint] = Field(default_factory=list, alias="evidencePoints")
    tags: List[str] = Field(default_factory=list)
    owner: Optional[str] = Field(default=None, max_length=100)


class ScenarioUpdate(ApiModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    business_goal: Optional[str] = Field(default=None, alias="businessGoal", min_length=1, max_length=2000)
    actors: Optional[List[str]] = None
    preconditions: Optional[List[str]] = None
    risk_level: Optional[RiskLevel] = Field(default=None, alias="riskLevel")
    invariants: Optional[List[str]] = None
    risks: Optional[List[Risk]] = None
    evidence_points: Optional[List[EvidencePoint]] = Field(default=None, alias="evidencePoints")
    tags: Optional[List[str]] = None
    owner: Optional[str] = Field(default=None, max_length=100)


class ReviewCreate(ApiModel):
    content: str = Field(min_length=1, max_length=4000)
    status: Literal["open", "resolved"] = "open"


class RejectRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=4000)


class ValidationStore:
    def __init__(self) -> None:
        self.scenarios: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def event(self, event_type: str, scenario: Dict[str, Any]) -> None:
        self.events.append(
            {
                "eventId": str(uuid4()),
                "eventType": event_type,
                "projectId": scenario["projectId"],
                "aggregateId": scenario["id"],
                "occurredAt": self.now(),
                "payload": {"scenario": self.public(scenario)},
            }
        )

    @staticmethod
    def public(scenario: Dict[str, Any]) -> Dict[str, Any]:
        result = deepcopy(scenario)
        result.pop("versions", None)
        return result

    def snapshot(self, scenario: Dict[str, Any]) -> None:
        version = deepcopy(self.public(scenario))
        version.pop("revision", None)
        version.pop("reviews", None)
        version.pop("auditLogs", None)
        scenario["versions"].append(version)

    def audit(self, scenario: Dict[str, Any], actor: str, action: str) -> None:
        scenario["auditLogs"].append(
            {"id": str(uuid4()), "actor": actor, "action": action, "occurredAt": self.now(), "version": scenario["version"]}
        )

    def update_snapshot_status(self, scenario: Dict[str, Any]) -> None:
        scenario["versions"][-1]["status"] = scenario["status"]
        scenario["versions"][-1]["updatedAt"] = scenario["updatedAt"]


store = ValidationStore()


def actor_role(x_openkate_role: str = Header(default="viewer")) -> Role:
    if x_openkate_role not in {"owner", "maintainer", "reviewer", "developer", "viewer"}:
        raise HTTPException(status_code=400, detail="invalid OpenKATE role")
    return x_openkate_role  # type: ignore[return-value]


def actor_name(x_openkate_actor: str = Header(default="local-user")) -> str:
    return x_openkate_actor


def require_role(*allowed: Role):
    def dependency(role: Role = Depends(actor_role)) -> Role:
        if role not in allowed:
            raise HTTPException(status_code=403, detail="permission denied")
        return role

    return dependency


def get_scenario(scenario_id: str) -> Dict[str, Any]:
    scenario = store.scenarios.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return scenario


def require_match(scenario: Dict[str, Any], if_match: Optional[str]) -> None:
    if if_match is None:
        raise HTTPException(status_code=428, detail={"code": "PRECONDITION_REQUIRED", "message": "If-Match is required"})
    supplied = if_match.removeprefix("W/").strip('"')
    if supplied != str(scenario["revision"]):
        raise HTTPException(
            status_code=409,
            detail={"code": "SCENARIO_VERSION_CONFLICT", "message": "scenario has been updated by another user"},
        )


def touch(scenario: Dict[str, Any], actor: str, action: str, snapshot: bool = False) -> None:
    scenario["revision"] += 1
    scenario["updatedAt"] = store.now()
    scenario["updatedBy"] = actor
    store.audit(scenario, actor, action)
    if snapshot:
        store.snapshot(scenario)
    else:
        store.update_snapshot_status(scenario)


def response_with_etag(response: Response, scenario: Dict[str, Any]) -> Dict[str, Any]:
    response.headers["ETag"] = f'"{scenario["revision"]}"'
    return store.public(scenario)


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "validation-service", "status": "ready"}


@app.post("/internal/v1/projects/{project_id}/scenarios", status_code=status.HTTP_201_CREATED)
async def create_scenario(
    project_id: str,
    payload: ScenarioCreate,
    response: Response,
    role: Role = Depends(require_role("owner", "maintainer", "developer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    now = store.now()
    scenario = {
        "id": f"scenario_{uuid4().hex[:12]}",
        "projectId": project_id,
        "status": "draft",
        "version": 1,
        "revision": 1,
        "createdBy": actor,
        "createdAt": now,
        "updatedBy": actor,
        "updatedAt": now,
        "reviews": [],
        "auditLogs": [],
        "versions": [],
        **payload.model_dump(by_alias=True),
    }
    scenario["owner"] = scenario["owner"] or actor
    store.audit(scenario, actor, "scenario.created")
    store.snapshot(scenario)
    store.scenarios[scenario["id"]] = scenario
    store.event("validation.scenario.created.v1", scenario)
    return response_with_etag(response, scenario)


@app.get("/internal/v1/projects/{project_id}/scenarios")
async def list_scenarios(
    project_id: str,
    q: Optional[str] = None,
    status_filter: Optional[ScenarioStatus] = Query(default=None, alias="status"),
    risk: Optional[RiskLevel] = None,
    tag: Optional[str] = None,
    owner: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
) -> Dict[str, Any]:
    items = [item for item in store.scenarios.values() if item["projectId"] == project_id]
    if q:
        query = q.lower()
        items = [item for item in items if query in item["title"].lower() or query in item["businessGoal"].lower()]
    if status_filter:
        items = [item for item in items if item["status"] == status_filter]
    if risk:
        items = [item for item in items if item["riskLevel"] == risk]
    if tag:
        items = [item for item in items if tag in item["tags"]]
    if owner:
        items = [item for item in items if item["owner"] == owner]
    items.sort(key=lambda item: item["updatedAt"], reverse=True)
    start = (page - 1) * page_size
    return {"items": [store.public(item) for item in items[start : start + page_size]], "total": len(items), "page": page, "pageSize": page_size}


@app.get("/internal/v1/scenarios/{scenario_id}")
async def scenario_detail(scenario_id: str, response: Response) -> Dict[str, Any]:
    return response_with_etag(response, get_scenario(scenario_id))


@app.patch("/internal/v1/scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    payload: ScenarioUpdate,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer", "developer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] == "in_review":
        raise HTTPException(status_code=409, detail="scenario in review cannot be edited")
    if scenario["status"] in {"archived", "deprecated"}:
        raise HTTPException(status_code=409, detail="archived scenario cannot be edited")
    scenario.update(payload.model_dump(by_alias=True, exclude_unset=True))
    scenario["version"] += 1
    scenario["status"] = "draft"
    touch(scenario, actor, "scenario.versioned", snapshot=True)
    store.event("validation.scenario.versioned.v1", scenario)
    return response_with_etag(response, scenario)


@app.post("/internal/v1/scenarios/{scenario_id}/submit-review")
async def submit_review(
    scenario_id: str,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer", "developer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] != "draft":
        raise HTTPException(status_code=409, detail="only draft scenarios can be submitted for review")
    scenario["status"] = "in_review"
    touch(scenario, actor, "scenario.review.requested")
    store.event("validation.scenario.review.requested.v1", scenario)
    return response_with_etag(response, scenario)


@app.post("/internal/v1/scenarios/{scenario_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_review(
    scenario_id: str,
    payload: ReviewCreate,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer", "reviewer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] != "in_review":
        raise HTTPException(status_code=409, detail="reviews require a scenario in review")
    review = {"id": f"review_{uuid4().hex[:12]}", "author": actor, "content": payload.content, "status": payload.status, "createdAt": store.now()}
    scenario["reviews"].append(review)
    touch(scenario, actor, "scenario.review.created")
    return response_with_etag(response, scenario)


@app.post("/internal/v1/scenarios/{scenario_id}/approve")
async def approve_scenario(
    scenario_id: str,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer", "reviewer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] != "in_review":
        raise HTTPException(status_code=409, detail="only scenarios in review can be approved")
    scenario["status"] = "approved"
    touch(scenario, actor, "scenario.approved")
    store.event("validation.scenario.approved.v1", scenario)
    return response_with_etag(response, scenario)


@app.post("/internal/v1/scenarios/{scenario_id}/reject")
async def reject_scenario(
    scenario_id: str,
    payload: RejectRequest,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer", "reviewer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] != "in_review":
        raise HTTPException(status_code=409, detail="only scenarios in review can be rejected")
    scenario["status"] = "rejected"
    scenario["reviews"].append({"id": f"review_{uuid4().hex[:12]}", "author": actor, "content": payload.reason, "status": "open", "createdAt": store.now()})
    touch(scenario, actor, "scenario.rejected")
    store.event("validation.scenario.rejected.v1", scenario)
    return response_with_etag(response, scenario)


@app.post("/internal/v1/scenarios/{scenario_id}/archive")
async def archive_scenario(
    scenario_id: str,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    role: Role = Depends(require_role("owner", "maintainer")),
    actor: str = Depends(actor_name),
) -> Dict[str, Any]:
    scenario = get_scenario(scenario_id)
    require_match(scenario, if_match)
    if scenario["status"] != "approved":
        raise HTTPException(status_code=409, detail="only approved scenarios can be archived")
    scenario["status"] = "archived"
    touch(scenario, actor, "scenario.archived")
    store.event("validation.scenario.archived.v1", scenario)
    return response_with_etag(response, scenario)


@app.get("/internal/v1/scenarios/{scenario_id}/versions")
async def list_versions(scenario_id: str) -> List[Dict[str, Any]]:
    return deepcopy(get_scenario(scenario_id)["versions"])


@app.get("/internal/v1/scenarios/{scenario_id}/diff")
async def scenario_diff(scenario_id: str, from_version: int = Query(alias="fromVersion", ge=1), to_version: int = Query(alias="toVersion", ge=1)) -> Dict[str, Any]:
    versions = get_scenario(scenario_id)["versions"]
    indexed = {item["version"]: item for item in versions}
    before, after = indexed.get(from_version), indexed.get(to_version)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="scenario version not found")
    ignored = {"id", "projectId", "version", "status", "createdAt", "updatedAt", "createdBy", "updatedBy"}
    fields = sorted((set(before) | set(after)) - ignored)
    return {
        "scenarioId": scenario_id,
        "fromVersion": from_version,
        "toVersion": to_version,
        "changes": [{"field": field, "from": before.get(field), "to": after.get(field)} for field in fields if before.get(field) != after.get(field)],
    }


@app.get("/internal/v1/events")
async def list_events() -> List[Dict[str, Any]]:
    return deepcopy(store.events)
