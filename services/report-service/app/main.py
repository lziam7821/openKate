from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="report-service", version="0.2.0")

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
    def __init__(self) -> None:
        self.models: Dict[str, Dict[str, Any]] = {}
        self.consumed_event_ids: Set[str] = set()

    def consume(self, event: Event) -> bool:
        if event.event_id in self.consumed_event_ids:
            return False
        self.consumed_event_ids.add(event.event_id)
        scenario = event.payload.get("scenario")
        if scenario:
            self.models[event.aggregate_id] = deepcopy(scenario)
        return True


store = ReportStore()


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "report-service", "status": "ready"}


@app.post("/internal/v1/events")
async def consume_event(event: Event) -> Dict[str, bool]:
    return {"accepted": store.consume(event)}


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
    items = [item for item in store.models.values() if item.get("projectId") == project_id]
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
