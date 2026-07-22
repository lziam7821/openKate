import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from openkate_common.service_app import instrument_app

app = FastAPI(title="governance-service", version="0.6.0")
instrument_app(app, "governance-service", ["failure-classification", "business-rules"])
FailureCategory = Literal["product", "environment", "data", "executor", "unknown"]
Role = Literal["owner", "maintainer", "reviewer", "developer", "viewer"]
RuleStatus = Literal["draft", "in_review", "approved", "published", "rolled_back"]
RiskLevel = Literal["low", "medium", "high", "critical"]


class ClassificationUpdate(BaseModel):
    category: FailureCategory
    reason: str = Field(min_length=1, max_length=2000)


class BadCaseCreate(BaseModel):
    evidence_refs: List[str] = Field(alias="evidenceRefs", min_length=1)
    description: str = Field(min_length=1, max_length=4000)


class RuleDraftCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=500)
    expected_effect: str = Field(alias="expectedEffect", min_length=1, max_length=2000)
    risk_level: RiskLevel = Field(default="medium", alias="riskLevel")


class RuleReview(BaseModel):
    decision: Literal["submit", "changes_requested", "revise"] = "submit"
    content: Optional[str] = Field(default=None, min_length=1, max_length=4000)


class ReplayRequest(BaseModel):
    run_ids: List[str] = Field(alias="runIds", min_length=1)


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


class BadCaseStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.items: Dict[str, Dict] = {}

    def create(self, run_id: str, payload: BadCaseCreate, actor: str) -> Dict:
        item = {"id": f"badcase_{uuid4().hex[:12]}", "runId": run_id, "evidenceRefs": payload.evidence_refs, "description": payload.description, "createdBy": actor, "createdAt": FailureStore.now()}
        self.items[item["id"]] = item
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO governance_schema.badcases (id, run_id, evidence_refs, description, created_by, created_at) VALUES (%s, %s, %s, %s, %s, %s)", (item["id"], run_id, Jsonb(payload.evidence_refs), payload.description, actor, item["createdAt"]))
        return deepcopy(item)

    def get(self, badcase_id: str) -> Optional[Dict]:
        if badcase_id in self.items or not self.database_url:
            return deepcopy(self.items.get(badcase_id))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute("SELECT id, run_id, evidence_refs, description, created_by, created_at FROM governance_schema.badcases WHERE id = %s", (badcase_id,)).fetchone()
        if row:
            self.items[badcase_id] = {"id": row["id"], "runId": row["run_id"], "evidenceRefs": row["evidence_refs"], "description": row["description"], "createdBy": row["created_by"], "createdAt": row["created_at"].isoformat()}
        return deepcopy(self.items.get(badcase_id))


class RuleStore:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.items: Dict[str, Dict] = {}

    @staticmethod
    def public(rule: Dict) -> Dict:
        return {key: deepcopy(value) for key, value in rule.items() if key not in {"approvals", "evaluations"}}

    def save(self, rule: Dict) -> Dict:
        self.items[rule["id"]] = deepcopy(rule)
        if self.database_url:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("INSERT INTO governance_schema.business_rules (id, badcase_id, status, risk_level, active_version, created_by, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, risk_level = EXCLUDED.risk_level, active_version = EXCLUDED.active_version, updated_at = EXCLUDED.updated_at", (rule["id"], rule["badcaseId"], rule["status"], rule["riskLevel"], rule["activeVersion"], rule["createdBy"], rule["createdAt"], rule["updatedAt"]))
                for version in rule["versions"]:
                    connection.execute("INSERT INTO governance_schema.rule_versions (rule_id, version, source_badcase_id, scope, expected_effect, content, created_by, created_at, published_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (rule_id, version) DO UPDATE SET published_at = EXCLUDED.published_at", (rule["id"], version["version"], rule["badcaseId"], Jsonb(version["scope"]), version["expectedEffect"], version["content"], version["createdBy"], version["createdAt"], version["publishedAt"]))
                for approval in rule["approvals"]:
                    connection.execute("INSERT INTO governance_schema.approvals (id, rule_id, rule_version, approver, created_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING", (approval["id"], rule["id"], approval["version"], approval["actor"], approval["createdAt"]))
                for evaluation in rule["evaluations"]:
                    connection.execute("INSERT INTO governance_schema.rule_evaluations (id, rule_id, rule_version, run_ids, new_hits, false_positives, false_negatives, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING", (evaluation["id"], rule["id"], evaluation["version"], Jsonb(evaluation["runIds"]), evaluation["newHits"], evaluation["falsePositives"], evaluation["falseNegatives"], evaluation["createdAt"]))
        return deepcopy(rule)

    def get(self, rule_id: str) -> Optional[Dict]:
        if rule_id in self.items or not self.database_url:
            return deepcopy(self.items.get(rule_id))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rule = connection.execute("SELECT id, badcase_id, status, risk_level, active_version, created_by, created_at, updated_at FROM governance_schema.business_rules WHERE id = %s", (rule_id,)).fetchone()
            if not rule:
                return None
            versions = connection.execute("SELECT version, scope, expected_effect, content, created_by, created_at, published_at FROM governance_schema.rule_versions WHERE rule_id = %s ORDER BY version", (rule_id,)).fetchall()
            approvals = connection.execute("SELECT id, rule_version, approver, created_at FROM governance_schema.approvals WHERE rule_id = %s ORDER BY created_at", (rule_id,)).fetchall()
            evaluations = connection.execute("SELECT id, rule_version, run_ids, new_hits, false_positives, false_negatives, created_at FROM governance_schema.rule_evaluations WHERE rule_id = %s ORDER BY created_at", (rule_id,)).fetchall()
        item = {"id": rule["id"], "badcaseId": rule["badcase_id"], "status": rule["status"], "riskLevel": rule["risk_level"], "activeVersion": rule["active_version"], "createdBy": rule["created_by"], "createdAt": rule["created_at"].isoformat(), "updatedAt": rule["updated_at"].isoformat(), "versions": [{"version": row["version"], "scope": row["scope"], "expectedEffect": row["expected_effect"], "content": row["content"], "createdBy": row["created_by"], "createdAt": row["created_at"].isoformat(), "publishedAt": row["published_at"].isoformat() if row["published_at"] else None} for row in versions], "approvals": [{"id": row["id"], "version": row["rule_version"], "actor": row["approver"], "createdAt": row["created_at"].isoformat()} for row in approvals], "evaluations": [{"id": row["id"], "version": row["rule_version"], "runIds": row["run_ids"], "newHits": row["new_hits"], "falsePositives": row["false_positives"], "falseNegatives": row["false_negatives"], "createdAt": row["created_at"].isoformat()} for row in evaluations]}
        self.items[rule_id] = item
        return deepcopy(item)

    def current_version(self, rule: Dict) -> Dict:
        return rule["versions"][-1]

    def draft(self, badcase: Dict, payload: RuleDraftCreate, actor: str) -> Dict:
        created_at = FailureStore.now()
        version = {"version": 1, "scope": {"description": payload.scope}, "expectedEffect": payload.expected_effect, "content": f"When {badcase['description']}, verify the correction before release.", "createdBy": actor, "createdAt": created_at, "publishedAt": None}
        return self.save({"id": f"rule_{uuid4().hex[:12]}", "badcaseId": badcase["id"], "status": "draft", "riskLevel": payload.risk_level, "activeVersion": None, "createdBy": actor, "createdAt": created_at, "updatedAt": created_at, "versions": [version], "approvals": [], "evaluations": []})


store = FailureStore(os.getenv("OPENKATE_GOVERNANCE_DATABASE_URL"))
badcase_store = BadCaseStore(os.getenv("OPENKATE_GOVERNANCE_DATABASE_URL"))
rule_store = RuleStore(os.getenv("OPENKATE_GOVERNANCE_DATABASE_URL"))


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


def get_rule(rule_id: str) -> Dict:
    rule = rule_store.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return rule


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
    return badcase_store.create(run_id, payload, x_openkate_actor)


@app.post("/internal/v1/badcases/{badcase_id}/rule-drafts", status_code=201)
async def create_rule_draft(badcase_id: str, payload: RuleDraftCreate, actor: str = Depends(actor_name), role: Role = Depends(require_role("owner", "maintainer", "reviewer", "developer"))) -> Dict:
    badcase = badcase_store.get(badcase_id)
    if badcase is None:
        raise HTTPException(status_code=404, detail="badcase not found")
    return RuleStore.public(rule_store.draft(badcase, payload, actor))


@app.get("/internal/v1/rules/{rule_id}")
async def rule_detail(rule_id: str) -> Dict:
    return RuleStore.public(get_rule(rule_id))


@app.post("/internal/v1/rules/{rule_id}/review")
async def review_rule(rule_id: str, payload: RuleReview, actor: str = Depends(actor_name), role: Role = Depends(require_role("owner", "maintainer", "reviewer"))) -> Dict:
    rule = get_rule(rule_id)
    version = rule_store.current_version(rule)
    if payload.decision == "submit" and rule["status"] == "draft":
        rule["status"] = "in_review"
    elif payload.decision in {"changes_requested", "revise"} and rule["status"] in {"in_review", "published"} and payload.content:
        rule["versions"].append({**version, "version": version["version"] + 1, "content": payload.content, "createdBy": actor, "createdAt": FailureStore.now(), "publishedAt": None})
        rule["status"] = "draft"
    else:
        raise HTTPException(status_code=409, detail="rule cannot be reviewed in its current state")
    rule["updatedAt"] = FailureStore.now()
    return RuleStore.public(rule_store.save(rule))


@app.post("/internal/v1/rules/{rule_id}/approve")
async def approve_rule(rule_id: str, actor: str = Depends(actor_name), role: Role = Depends(require_role("owner", "maintainer", "reviewer"))) -> Dict:
    rule = get_rule(rule_id)
    version = rule_store.current_version(rule)
    if rule["status"] != "in_review":
        raise HTTPException(status_code=409, detail="rule is not awaiting approval")
    if actor == rule["createdBy"]:
        raise HTTPException(status_code=403, detail="rule author cannot approve the rule")
    if any(item["version"] == version["version"] and item["actor"] == actor for item in rule["approvals"]):
        raise HTTPException(status_code=409, detail="actor already approved this version")
    rule["approvals"].append({"id": str(uuid4()), "version": version["version"], "actor": actor, "createdAt": FailureStore.now()})
    required = 2 if rule["riskLevel"] in {"high", "critical"} else 1
    if len([item for item in rule["approvals"] if item["version"] == version["version"]]) >= required:
        rule["status"] = "approved"
    rule["updatedAt"] = FailureStore.now()
    return RuleStore.public(rule_store.save(rule))


@app.post("/internal/v1/rules/{rule_id}/replay")
async def replay_rule(rule_id: str, payload: ReplayRequest, role: Role = Depends(require_role("owner", "maintainer", "reviewer"))) -> Dict:
    rule = get_rule(rule_id)
    if rule["status"] not in {"in_review", "approved"}:
        raise HTTPException(status_code=409, detail="rule must be reviewed before replay")
    version = rule_store.current_version(rule)
    evaluation = {"id": str(uuid4()), "version": version["version"], "runIds": list(dict.fromkeys(payload.run_ids)), "newHits": len(set(payload.run_ids)), "falsePositives": 0, "falseNegatives": 0, "createdAt": FailureStore.now()}
    rule["evaluations"].append(evaluation)
    rule["updatedAt"] = FailureStore.now()
    rule_store.save(rule)
    return deepcopy(evaluation)


@app.post("/internal/v1/rules/{rule_id}/publish")
async def publish_rule(rule_id: str, role: Role = Depends(require_role("owner", "maintainer"))) -> Dict:
    rule = get_rule(rule_id)
    version = rule_store.current_version(rule)
    if rule["status"] != "approved":
        raise HTTPException(status_code=409, detail="rule is not approved")
    if not any(item["version"] == version["version"] for item in rule["evaluations"]):
        raise HTTPException(status_code=409, detail="rule must be replayed before publication")
    version["publishedAt"] = FailureStore.now()
    rule["activeVersion"], rule["status"], rule["updatedAt"] = version["version"], "published", FailureStore.now()
    return RuleStore.public(rule_store.save(rule))


@app.post("/internal/v1/rules/{rule_id}/rollback")
async def rollback_rule(rule_id: str, role: Role = Depends(require_role("owner", "maintainer"))) -> Dict:
    rule = get_rule(rule_id)
    if rule["status"] != "published":
        raise HTTPException(status_code=409, detail="only a published rule can be rolled back")
    prior = [item for item in rule["versions"] if item["publishedAt"] and item["version"] != rule["activeVersion"]]
    rule["activeVersion"] = prior[-1]["version"] if prior else None
    rule["status"], rule["updatedAt"] = "rolled_back", FailureStore.now()
    return RuleStore.public(rule_store.save(rule))


@app.get("/internal/v1/rules/{rule_id}/metrics")
async def rule_metrics(rule_id: str) -> Dict:
    rule = get_rule(rule_id)
    evaluations = [item for item in rule["evaluations"] if item["version"] == rule["activeVersion"]]
    runs = sum(len(item["runIds"]) for item in evaluations)
    hits = sum(item["newHits"] for item in evaluations)
    false_positives = sum(item["falsePositives"] for item in evaluations)
    false_negatives = sum(item["falseNegatives"] for item in evaluations)
    return {"ruleId": rule_id, "activeVersion": rule["activeVersion"], "hitRate": hits / runs if runs else 0, "falsePositiveRate": false_positives / hits if hits else 0, "falseNegatives": false_negatives, "recentUsage": {"replays": len(evaluations), "runs": runs}}
