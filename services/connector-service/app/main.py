import hashlib
import hmac
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Literal, Optional
from uuid import uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

Provider = Literal["github", "gitlab"]
Role = Literal["owner", "maintainer", "reviewer", "developer", "viewer"]
app = FastAPI(title="connector-service", version="0.7.0")
instrument_app(app, "connector-service", ["connectors", "webhooks"])


class ConnectorCreate(BaseModel):
    provider: Provider
    repository: str = Field(min_length=1, max_length=500)
    secret_ref: str = Field(alias="secretRef", min_length=1, max_length=300)


class ConnectorStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.connectors: Dict[str, Dict] = {}
        self.deliveries: Dict[str, Dict] = {}

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
    return {"deliveryId": delivery_id, "status": "accepted" if accepted else "duplicate"}
