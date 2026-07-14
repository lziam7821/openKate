import os
from typing import Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.store import ProjectStore
from openkate_common.service_app import instrument_app

app = FastAPI(title="project-service", version="0.1.0")
instrument_app(app, "project-service", ["workspace", "project", "environment", "member", "audit"])

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


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    base_url: Optional[str] = Field(default=None, min_length=8, max_length=300)
    write_policy: Optional[Literal["deny", "read_only", "approval_required"]] = None
    allowed_hosts: Optional[List[str]] = None
    account_refs: Optional[List[str]] = None
    data_set_refs: Optional[List[str]] = None
    secret_refs: Optional[Dict[str, str]] = None


class MemberCreate(BaseModel):
    user_id: str = Field(min_length=2, max_length=100)
    role: Role


class MemberUpdate(BaseModel):
    role: Role


store = ProjectStore(os.getenv("OPENKATE_PROJECT_DATABASE_URL"))


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


def require_owner(role: Role = Depends(actor_role)) -> Role:
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner permission required")
    return role


def require_project_role(project_id: str, actor: str, allowed: set[str]) -> None:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if store.member_role(project_id, actor) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="project permission required")


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "project-service", "status": "ready" if store.ready() else "degraded"}


@app.get("/internal/v1/workspaces")
async def list_workspaces() -> List[Dict[str, str]]:
    return store.list_workspaces()


@app.post("/internal/v1/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(payload: WorkspaceCreate, role: Role = Depends(require_owner)) -> Dict[str, str]:
    return store.create_workspace(payload.name)


@app.get("/internal/v1/workspaces/{workspace_id}/projects")
async def list_projects(workspace_id: str) -> List[Dict[str, object]]:
    return store.list_projects(workspace_id)


@app.post("/internal/v1/workspaces/{workspace_id}/projects", status_code=status.HTTP_201_CREATED)
async def create_project(workspace_id: str, payload: ProjectCreate, role: Role = Depends(require_owner), actor: str = Depends(actor_name)) -> Dict[str, object]:
    project = store.create_project(workspace_id, payload.name, payload.description, actor, role)
    if project is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return project


@app.get("/internal/v1/projects/{project_id}")
async def get_project(project_id: str, actor: str = Depends(actor_name)) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner", "maintainer", "reviewer", "developer", "viewer"})
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.patch("/internal/v1/projects/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner"})
    project = store.update_project(project_id, payload.model_dump(exclude_none=True), actor)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.post("/internal/v1/projects/{project_id}/archive")
async def archive_project(project_id: str, role: Role = Depends(require_owner), actor: str = Depends(actor_name)) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner"})
    project = store.archive_project(project_id, actor)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.post("/internal/v1/projects/{project_id}/environments", status_code=status.HTTP_201_CREATED)
async def create_environment(project_id: str, payload: EnvironmentCreate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner", "maintainer"})
    environment = store.create_environment(project_id, payload.model_dump(), actor)
    if environment is None:
        raise HTTPException(status_code=404, detail="project not found")
    return environment


@app.get("/internal/v1/projects/{project_id}/environments")
async def list_environments(project_id: str, actor: str = Depends(actor_name)) -> List[Dict[str, object]]:
    require_project_role(project_id, actor, {"owner", "maintainer", "reviewer", "developer", "viewer"})
    environments = store.list_environments(project_id)
    if environments is None:
        raise HTTPException(status_code=404, detail="project not found")
    return environments


@app.patch("/internal/v1/projects/{project_id}/environments/{environment_id}")
async def update_environment(
    project_id: str,
    environment_id: str,
    payload: EnvironmentUpdate,
    role: Role = Depends(require_write),
    actor: str = Depends(actor_name),
) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner", "maintainer"})
    environment = store.update_environment(project_id, environment_id, payload.model_dump(exclude_none=True), actor)
    if environment is None:
        raise HTTPException(status_code=404, detail="environment not found")
    return environment


@app.get("/internal/v1/projects/{project_id}/environments/{environment_id}")
async def get_environment(project_id: str, environment_id: str, actor: str = Depends(actor_name)) -> Dict[str, object]:
    require_project_role(project_id, actor, {"owner", "maintainer", "reviewer", "developer", "viewer"})
    environment = store.get_environment(project_id, environment_id)
    if environment is None:
        raise HTTPException(status_code=404, detail="environment not found")
    return environment


@app.get("/internal/v1/projects/{project_id}/members")
async def list_members(project_id: str, actor: str = Depends(actor_name)) -> List[Dict[str, str]]:
    require_project_role(project_id, actor, {"owner", "maintainer", "reviewer", "developer", "viewer"})
    members = store.list_members(project_id)
    if members is None:
        raise HTTPException(status_code=404, detail="project not found")
    return members


@app.post("/internal/v1/projects/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def create_member(project_id: str, payload: MemberCreate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, str]:
    require_project_role(project_id, actor, {"owner"})
    member = store.create_member(project_id, payload.user_id, payload.role, actor)
    if member is None:
        raise HTTPException(status_code=404, detail="project not found")
    return member


@app.patch("/internal/v1/projects/{project_id}/members/{user_id}")
async def update_member(project_id: str, user_id: str, payload: MemberUpdate, role: Role = Depends(require_write), actor: str = Depends(actor_name)) -> Dict[str, str]:
    require_project_role(project_id, actor, {"owner"})
    member = store.update_member(project_id, user_id, payload.role, actor)
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    return member


@app.delete("/internal/v1/projects/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(project_id: str, user_id: str, role: Role = Depends(require_owner), actor: str = Depends(actor_name)) -> None:
    require_project_role(project_id, actor, {"owner"})
    if not store.delete_member(project_id, user_id, actor):
        raise HTTPException(status_code=404, detail="member not found")


@app.get("/internal/v1/projects/{project_id}/audit-logs")
async def list_audit_logs(project_id: str, actor: str = Depends(actor_name)) -> List[Dict[str, str]]:
    require_project_role(project_id, actor, {"owner", "maintainer"})
    audit_logs = store.list_audit_logs(project_id)
    if audit_logs is None:
        raise HTTPException(status_code=404, detail="project not found")
    return audit_logs
