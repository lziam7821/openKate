import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openkate_common.service_app import instrument_app

app = FastAPI(title="agent-service", version="0.5.0")
instrument_app(app, "agent-service", ["scenario-generation"])
ASSET_SERVICE_URL = os.getenv("OPENKATE_ASSET_SERVICE_URL", "http://127.0.0.1:8006")
VALIDATION_SERVICE_URL = os.getenv("OPENKATE_VALIDATION_SERVICE_URL", "http://127.0.0.1:8002")
tasks: Dict[str, Dict[str, Any]] = {}
knowledge: Dict[str, List[Dict[str, Any]]] = {}


class GenerationCreate(BaseModel):
    asset_ids: List[str] = Field(alias="assetIds", min_length=1)


class ReviewDecision(BaseModel):
    decision: Literal["approve", "changes_requested"]
    comment: str = Field(default="", max_length=2000)


class KnowledgeImport(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=10000)
    source: str = Field(min_length=1, max_length=500)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def draft_from_assets(project_id: str, assets: List[Dict[str, Any]]) -> Dict[str, Any]:
    citations = [citation for asset in assets for citation in asset.get("parse", {}).get("citations", [])]
    texts = [asset.get("parse", {}).get("text", "") for asset in assets]
    lines = [line.strip().lstrip("#").strip() for text in texts for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else "Generated validation scenario"
    goal = lines[1] if len(lines) > 1 else title
    return {"title": title[:200], "businessGoal": goal[:2000], "actors": ["qa"], "preconditions": [], "riskLevel": "medium", "invariants": [], "risks": [{"title": "Source coverage", "description": "Confirm all source requirements are covered.", "level": "medium"}], "evidencePoints": [], "tags": ["ai-draft"], "projectId": project_id, "citations": citations or [{"source": "inferred", "kind": "inferred"}], "quality": 0.8 if citations else 0.5}


async def parsed_assets(asset_ids: List[str]) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        assets = []
        for asset_id in asset_ids:
            response = await client.get(f"{ASSET_SERVICE_URL}/internal/v1/assets/{asset_id}/document")
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"asset not found: {asset_id}")
            response.raise_for_status()
            asset = response.json()
            if not asset.get("parse"):
                raise HTTPException(status_code=409, detail=f"asset must be parsed before generation: {asset_id}")
            assets.append(asset)
    return assets


def event(task: Dict[str, Any], event_type: str, payload: Dict[str, Any]) -> None:
    task["events"].append({"eventId": str(uuid4()), "eventType": event_type, "occurredAt": now(), "payload": payload})


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"service": "agent-service", "status": "ready"}


@app.post("/internal/v1/projects/{project_id}/knowledge/imports", status_code=201)
async def import_knowledge(project_id: str, payload: KnowledgeImport) -> Dict[str, Any]:
    item = {"id": f"knowledge_{uuid4().hex[:12]}", "projectId": project_id, **payload.model_dump(), "createdAt": now()}
    knowledge.setdefault(project_id, []).append(item)
    return item


@app.get("/internal/v1/projects/{project_id}/knowledge")
async def list_knowledge(project_id: str, q: str = "") -> Dict[str, Any]:
    terms = [term.lower() for term in q.split()]
    items = [item for item in knowledge.get(project_id, []) if all(term in f"{item['title']} {item['content']}".lower() for term in terms)]
    return {"items": items, "snapshot": {"projectId": project_id, "ids": [item["id"] for item in items]}}


@app.post("/internal/v1/projects/{project_id}/scenario-generations", status_code=202)
async def create_generation(project_id: str, payload: GenerationCreate) -> Dict[str, Any]:
    assets = await parsed_assets(payload.asset_ids)
    task_id = f"generation_{uuid4().hex[:12]}"
    task = {"id": task_id, "projectId": project_id, "assetIds": payload.asset_ids, "status": "needs_review", "createdAt": now(), "updatedAt": now(), "events": [], "draft": draft_from_assets(project_id, assets), "review": None, "model": "source-grounded-rule-based", "cost": {"inputTokens": 0, "outputTokens": 0, "latencyMs": 0}}
    event(task, "agent.generation.requested.v1", {"assetIds": payload.asset_ids})
    event(task, "agent.stage.completed.v1", {"stage": "scenario_planner", "quality": task["draft"]["quality"]})
    event(task, "agent.review.required.v1", {})
    tasks[task_id] = task
    return public(task)


def public(task: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in task.items() if key != "events"}


def get(task_id: str) -> Dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="generation task not found")
    return task


@app.get("/internal/v1/scenario-generations/{task_id}")
async def generation_detail(task_id: str) -> Dict[str, Any]:
    return public(get(task_id))


@app.get("/internal/v1/scenario-generations/{task_id}/events")
async def generation_events(task_id: str) -> Dict[str, Any]:
    task = get(task_id)
    return {"events": task["events"]}


@app.post("/internal/v1/scenario-generations/{task_id}/review")
async def review_generation(task_id: str, payload: ReviewDecision) -> Dict[str, Any]:
    task = get(task_id)
    if task["status"] != "needs_review":
        raise HTTPException(status_code=409, detail="generation is not awaiting review")
    task["review"] = payload.model_dump(); task["updatedAt"] = now()
    return public(task)


@app.post("/internal/v1/scenario-generations/{task_id}/accept")
async def accept_generation(task_id: str) -> Dict[str, Any]:
    task = get(task_id)
    if task["status"] != "needs_review" or task["draft"]["quality"] < 0.75:
        raise HTTPException(status_code=409, detail="generation cannot be accepted")
    draft = {key: value for key, value in task["draft"].items() if key not in {"projectId", "citations", "quality"}}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(f"{VALIDATION_SERVICE_URL}/internal/v1/projects/{task['projectId']}/scenarios", headers={"X-OpenKATE-Role": "developer", "X-OpenKATE-Actor": "agent-review"}, json=draft)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="validation service unavailable") from error
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail="generated draft could not be imported")
    task["scenarioId"] = response.json()["id"]
    task["status"] = "accepted"; task["updatedAt"] = now(); event(task, "agent.generation.accepted.v1", {"scenarioId": task["scenarioId"]})
    return public(task)


@app.post("/internal/v1/scenario-generations/{task_id}/reject")
async def reject_generation(task_id: str) -> Dict[str, Any]:
    task = get(task_id); task["status"] = "rejected"; task["updatedAt"] = now(); event(task, "agent.generation.rejected.v1", {})
    return public(task)
