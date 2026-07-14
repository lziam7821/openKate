from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

app = FastAPI(title="project-service", version="0.1.0")

Role = Literal["owner", "maintainer", "reviewer", "developer", "viewer"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    base_url: str = Field(min_length=8, max_length=300)
    write_policy: Literal["deny", "read_only", "approval_required"] = "deny"
    allowed_hosts: List[str] = Field(default_factory=list)
    account_refs: List[str] = Field(default_factory=list)
    data_set_refs: List[str] = Field(default_factory=list)
    secret_refs: Dict[str, str] = Field(default_factory=dict)


class MemberCreate(BaseModel):
    user_id: str = Field(min_length=2, max_length=100)
    role: Role


class MemberUpdate(BaseModel):
    role: Role


class ProjectStore:
    def __init__(self) -> None:
        self.workspaces: Dict[str, Dict[str, str]] = {
            "workspace_demo": {"id": "workspace_demo", "name": "OpenKATE Demo"}
        }
        now = self.now()
        self.projects: Dict[str, Dict[str, object]] = {
            "project_demo": {"id": "project_demo", "workspaceId": "workspace_demo", "name": "OpenKATE Demo", "description": "Execution Fabric demo project", "createdAt": now, "updatedAt": now}
        }
        self.environments: Dict[str, List[Dict[str, object]]] = {}
        self.members: Dict[str, List[Dict[str, str]]] = {"project_demo": [{"userId": "local-owner", "role": "owner"}]}
        self.audit_logs: Dict[str, List[Dict[str, str]]] = {}

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def audit(self, project_id: str, actor: str, action: str) -> None:
        self.audit_logs.setdefault(project_id, []).append(
            {"id": str(uuid4()), "actor": actor, "action": action, "occurredAt": self.now()}
        )


store = ProjectStore()


def actor_role(x_openkate_role: str = Header(default="viewer")) -> Role:
    if x_openkate_role not in {"owner", "maintainer", "reviewer", "developer", "viewer"}:
        raise HTTPException(status_code=400, detail="invalid OpenKATE role")
    return x_openkate_role  # type: ignore[return-value]


def actor_name(x_openkate_actor: str = Header(default="local-user")) -> str:
    return x_openkate_actor


def require_write(role: Role = Depends(actor_role)) -> Role:
    if role not in {"owner", "maintainer"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="write permission required")
    return role


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "project-service", "status": "ready"}


@app.get("/internal/v1/workspaces")
async def list_workspaces() -> List[Dict[str, str]]:
    return list(store.workspaces.values())


@app.post("/internal/v1/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(payload: WorkspaceCreate, role: Role = Depends(require_write)) -> Dict[str, str]:
    workspace = {"id": f"workspace_{uuid4().hex[:12]}", "name": payload.name}
    store.workspaces[workspace["id"]] = workspace
    return workspace


@app.get("/internal/v1/workspaces/{workspace_id}/projects")
async def list_projects(workspace_id: str) -> List[Dict[str, object]]:
    return [item for item in store.projects.values() if item["workspaceId"] == workspace_id]


@app.post("/internal/v1/workspaces/{workspace_id}/projects", status_code=status.HTTP_201_CREATED)
async def create_project(workspace_id: str, payload: ProjectCreate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, object]:
    if workspace_id not in store.workspaces:
        raise HTTPException(status_code=404, detail="workspace not found")
    project_id = f"project_{uuid4().hex[:12]}"
    project = {
        "id": project_id,
        "workspaceId": workspace_id,
        "name": payload.name,
        "description": payload.description,
        "createdAt": store.now(),
        "updatedAt": store.now(),
    }
    store.projects[project_id] = project
    store.members[project_id] = [{"userId": actor, "role": role}]
    store.audit(project_id, actor, "project.created")
    return project


@app.get("/internal/v1/projects/{project_id}")
async def get_project(project_id: str) -> Dict[str, object]:
    project = store.projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.patch("/internal/v1/projects/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, object]:
    project = await get_project(project_id)
    updates = payload.model_dump(exclude_none=True)
    project.update(updates)
    project["updatedAt"] = store.now()
    store.audit(project_id, actor, "project.updated")
    return project


@app.post("/internal/v1/projects/{project_id}/environments", status_code=status.HTTP_201_CREATED)
async def create_environment(project_id: str, payload: EnvironmentCreate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, object]:
    await get_project(project_id)
    environment = {"id": f"env_{uuid4().hex[:12]}", **payload.model_dump()}
    store.environments.setdefault(project_id, []).append(environment)
    store.audit(project_id, actor, "environment.created")
    return environment


@app.get("/internal/v1/projects/{project_id}/environments")
async def list_environments(project_id: str) -> List[Dict[str, object]]:
    await get_project(project_id)
    return store.environments.get(project_id, [])


@app.get("/internal/v1/projects/{project_id}/environments/{environment_id}")
async def get_environment(project_id: str, environment_id: str) -> Dict[str, object]:
    await get_project(project_id)
    for environment in store.environments.get(project_id, []):
        if environment["id"] == environment_id:
            return environment
    raise HTTPException(status_code=404, detail="environment not found")


@app.get("/internal/v1/projects/{project_id}/members")
async def list_members(project_id: str) -> List[Dict[str, str]]:
    await get_project(project_id)
    return store.members.get(project_id, [])


@app.post("/internal/v1/projects/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def create_member(project_id: str, payload: MemberCreate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, str]:
    await get_project(project_id)
    member = {"userId": payload.user_id, "role": payload.role}
    store.members.setdefault(project_id, []).append(member)
    store.audit(project_id, actor, "member.created")
    return member


@app.patch("/internal/v1/projects/{project_id}/members/{user_id}")
async def update_member(project_id: str, user_id: str, payload: MemberUpdate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, str]:
    await get_project(project_id)
    for member in store.members.get(project_id, []):
        if member["userId"] == user_id:
            member["role"] = payload.role
            store.audit(project_id, actor, "member.updated")
            return member
    raise HTTPException(status_code=404, detail="member not found")


@app.get("/internal/v1/projects/{project_id}/audit-logs")
async def list_audit_logs(project_id: str) -> List[Dict[str, str]]:
    await get_project(project_id)
    return list(reversed(store.audit_logs.get(project_id, [])))
