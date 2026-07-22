import hashlib
import hmac
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from uuid import uuid4

import psycopg
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

Provider = Literal["github", "gitlab"]
Role = Literal["owner", "maintainer", "reviewer", "developer", "viewer"]
app = FastAPI(title="connector-service", version="0.7.0")
instrument_app(app, "connector-service", ["connectors", "webhooks"])
VALIDATION_SERVICE_URL = os.getenv("OPENKATE_VALIDATION_SERVICE_URL", "http://127.0.0.1:8002")


class ConnectorCreate(BaseModel):
    provider: Provider
    repository: str = Field(min_length=1, max_length=500)
    secret_ref: str = Field(alias="secretRef", min_length=1, max_length=300)


class CiTrigger(BaseModel):
    targets: List[str] = Field(min_length=1)
    pull_request_id: Optional[str] = Field(default=None, alias="pullRequestId")
    commit_sha: Optional[str] = Field(default=None, alias="commitSha")


class ConnectorStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.connectors: Dict[str, Dict] = {}
        self.deliveries: Dict[str, Dict] = {}
        self.pull_requests: Dict[str, Dict] = {}
        self.ci_runs: Dict[str, Dict] = {}

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, project_id: str, payload: ConnectorCreate) -> Dict:
        item = {"id": f"connector_{uuid4().hex[:12]}", "projectId": project_id, "provider": payload.provider, "repository": payload.repository, "secretRef": payload.secret_ref, "createdAt": self.now()}
        self.connectors[item["id"]] = item
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO connector_schema.connectors (id, project_id, provider, repository, secret_ref, created_at) VALUES (%s, %s, %s, %s, %s, %s)", (item["id"], project_id, payload.provider, payload.repository, payload.secret_ref, item["createdAt"]))
        return deepcopy(item)

    def find(self, project_id: str, provider: Provider) -> Optional[Dict]:
        candidates = [item for item in self.connectors.values() if item["projectId"] == project_id and item["provider"] == provider]
        if candidates or not self.database_url:
            return deepcopy(candidates[0]) if candidates else None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT id, project_id, provider, repository, secret_ref, created_at FROM connector_schema.connectors WHERE project_id = %s AND provider = %s ORDER BY created_at LIMIT 1", (project_id, provider)).fetchone()
        if not row:
            return None
        item = {"id": row["id"], "projectId": row["project_id"], "provider": row["provider"], "repository": row["repository"], "secretRef": row["secret_ref"], "createdAt": row["created_at"].isoformat()}
        self.connectors[item["id"]] = item
        return deepcopy(item)

    def record_delivery(self, connector: Dict, delivery_id: str, payload: Dict) -> bool:
        key = f"{connector['id']}:{delivery_id}"
        if key in self.deliveries:
            return False
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute("INSERT INTO connector_schema.webhook_deliveries (id, connector_id, delivery_id, payload) VALUES (%s, %s, %s, %s) ON CONFLICT (connector_id, delivery_id) DO NOTHING RETURNING id", (str(uuid4()), connector["id"], delivery_id, Jsonb(payload))).fetchone()
            if row is None:
                return False
        self.deliveries[key] = {"connectorId": connector["id"], "deliveryId": delivery_id, "payload": deepcopy(payload)}
        return True

    def record_pull_request(self, connector: Dict, delivery_id: str, event: Dict) -> Dict:
        item = {"id": f"pr_{connector['id']}_{event['number']}", "connectorId": connector["id"], "projectId": connector["projectId"], "deliveryId": delivery_id, **event, "receivedAt": self.now()}
        self.pull_requests[item["id"]] = item
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO connector_schema.pull_requests (id, connector_id, project_id, delivery_id, number, action, head_sha, payload, received_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET delivery_id = EXCLUDED.delivery_id, action = EXCLUDED.action, head_sha = EXCLUDED.head_sha, payload = EXCLUDED.payload, received_at = EXCLUDED.received_at", (item["id"], connector["id"], connector["projectId"], delivery_id, event["number"], event["action"], event["headSha"], Jsonb(event["payload"]), item["receivedAt"]))
        return deepcopy(item)

    def save_ci_run(self, item: Dict) -> Dict:
        self.ci_runs[item["id"]] = deepcopy(item)
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO connector_schema.ci_runs (id, project_id, pull_request_id, commit_sha, targets, scenario_ids, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET scenario_ids = EXCLUDED.scenario_ids, status = EXCLUDED.status", (item["id"], item["projectId"], item["pullRequestId"], item["commitSha"], Jsonb(item["targets"]), item["scenarioIds"], item["status"], item["createdAt"]))
        return deepcopy(item)


store = ConnectorStore(os.getenv("OPENKATE_CONNECTOR_DATABASE_URL"))


def actor_role(x_openkate_role: str = Header(default="viewer")) -> Role:
    if x_openkate_role not in {"owner", "maintainer", "reviewer", "developer", "viewer"}:
        raise HTTPException(status_code=400, detail="invalid OpenKATE role")
    return x_openkate_role  # type: ignore[return-value]


def require_write(role: Role = Depends(actor_role)) -> Role:
    if role not in {"owner", "maintainer"}:
        raise HTTPException(status_code=403, detail="connector write permission required")
    return role


def secret_for(secret_ref: str) -> Optional[str]:
    return json.loads(os.getenv("OPENKATE_WEBHOOK_SECRETS", "{}")) .get(secret_ref)


def valid_signature(provider: Provider, headers: Dict[str, str], body: bytes, secret: str) -> bool:
    if provider == "github":
        supplied = headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)
    return hmac.compare_digest(headers.get("x-gitlab-token", ""), secret)


def pull_request_event(provider: Provider, payload: Dict) -> Optional[Dict]:
    if provider == "github" and payload.get("pull_request"):
        pull_request = payload["pull_request"]
        return {"number": int(pull_request["number"] if "number" in pull_request else payload["number"]), "action": payload.get("action", "updated"), "headSha": pull_request["head"]["sha"], "payload": payload}
    if provider == "gitlab" and payload.get("object_attributes", {}).get("iid"):
        attributes = payload["object_attributes"]
        return {"number": int(attributes["iid"]), "action": attributes.get("action", "updated"), "headSha": attributes.get("last_commit", {}).get("id", ""), "payload": payload}
    return None


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"service": "connector-service", "status": "ready"}


@app.post("/internal/v1/projects/{project_id}/connectors", status_code=201)
async def create_connector(project_id: str, payload: ConnectorCreate, role: Role = Depends(require_write)) -> Dict:
    return store.create(project_id, payload)


@app.post("/internal/v1/webhooks/{provider}/{project_id}", status_code=202)
async def receive_webhook(provider: Provider, project_id: str, request: Request) -> Dict:
    connector = store.find(project_id, provider)
    if connector is None:
        raise HTTPException(status_code=404, detail="connector not found")
    secret = secret_for(connector["secretRef"])
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret is unavailable")
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    if not valid_signature(provider, headers, body, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    delivery_id = headers.get("x-github-delivery") if provider == "github" else headers.get("x-gitlab-event-uuid")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="webhook delivery id is required")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="webhook body must be JSON") from error
    accepted = store.record_delivery(connector, delivery_id, payload)
    event = pull_request_event(provider, payload) if accepted else None
    pull_request = store.record_pull_request(connector, delivery_id, event) if event else None
    return {"deliveryId": delivery_id, "status": "accepted" if accepted else "duplicate", "pullRequestId": pull_request["id"] if pull_request else None}


@app.post("/internal/v1/ci/projects/{project_id}/trigger", status_code=202)
async def trigger_ci(project_id: str, payload: CiTrigger, role: Role = Depends(require_write)) -> Dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{VALIDATION_SERVICE_URL}/internal/v1/projects/{project_id}/scenarios/impacted", params=[("targets", target) for target in payload.targets])
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="validation service unavailable") from error
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail="impact analysis failed")
    impact = response.json()
    item = {"id": f"ci_{uuid4().hex[:12]}", "projectId": project_id, "pullRequestId": payload.pull_request_id, "commitSha": payload.commit_sha, "targets": payload.targets, "scenarioIds": [scenario["id"] for scenario in impact["items"]], "status": "needs_confirmation" if impact["fallbackRequired"] else "queued", "createdAt": store.now()}
    return store.save_ci_run(item)


@app.get("/internal/v1/ci/runs/{run_id}/status")
async def ci_status(run_id: str) -> Dict:
    item = store.ci_runs.get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="ci run not found")
    return deepcopy(item)
