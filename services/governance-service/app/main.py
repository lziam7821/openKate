import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

app = FastAPI(title="governance-service", version="0.4.0")
instrument_app(app, "governance-service", ["failure-classification"])
FailureCategory = Literal["product", "environment", "data", "executor", "unknown"]


class ClassificationUpdate(BaseModel):
    category: FailureCategory
    reason: str = Field(min_length=1, max_length=2000)


class BadCaseCreate(BaseModel):
    evidence_refs: List[str] = Field(alias="evidenceRefs", min_length=1)
    description: str = Field(min_length=1, max_length=4000)


class FailureStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.items: Dict[str, Dict] = {}

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def classify(self, failure_id: str, payload: ClassificationUpdate, actor: str) -> Dict:
        previous = self.get(failure_id)
        item = previous or {"id": failure_id, "category": "unknown", "reason": "unclassified", "audit": []}
        item["audit"].append({"id": str(uuid4()), "actor": actor, "from": item["category"], "to": payload.category, "reason": payload.reason, "occurredAt": self.now()})
        item["category"], item["reason"] = payload.category, payload.reason
        self.items[failure_id] = item
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO governance_schema.failure_classifications (failure_id, category, reason, actor, audit) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (failure_id) DO UPDATE SET category = EXCLUDED.category, reason = EXCLUDED.reason, actor = EXCLUDED.actor, audit = EXCLUDED.audit", (failure_id, item["category"], item["reason"], actor, Jsonb(item["audit"])))
        return deepcopy(item)

    def get(self, failure_id: str) -> Optional[Dict]:
        if failure_id in self.items or not self.database_url:
            return deepcopy(self.items.get(failure_id))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT category, reason, audit FROM governance_schema.failure_classifications WHERE failure_id = %s", (failure_id,)).fetchone()
        if row:
            self.items[failure_id] = {"id": failure_id, "category": row["category"], "reason": row["reason"], "audit": row["audit"]}
        return deepcopy(self.items.get(failure_id))


store = FailureStore(os.getenv("OPENKATE_GOVERNANCE_DATABASE_URL"))
badcases: Dict[str, Dict] = {}


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"service": "governance-service", "status": "ready"}


@app.get("/internal/v1/failures/{failure_id}")
async def failure_detail(failure_id: str) -> Dict:
    failure = store.get(failure_id)
    if failure is None:
        raise HTTPException(status_code=404, detail="failure not found")
    return failure


@app.post("/internal/v1/failures/{failure_id}/classification")
async def classify_failure(failure_id: str, payload: ClassificationUpdate, x_openkate_actor: str = Header(default="local-user")) -> Dict:
    return store.classify(failure_id, payload, x_openkate_actor)


@app.post("/internal/v1/runs/{run_id}/badcases", status_code=201)
async def create_badcase(run_id: str, payload: BadCaseCreate, x_openkate_actor: str = Header(default="local-user")) -> Dict:
    item = {"id": f"badcase_{uuid4().hex[:12]}", "runId": run_id, "evidenceRefs": payload.evidence_refs, "description": payload.description, "createdBy": x_openkate_actor, "createdAt": FailureStore.now()}
    badcases[item["id"]] = item
    return item
