import os
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

PROJECT_SERVICE_URL = os.getenv("OPENKATE_PROJECT_SERVICE_URL", "http://127.0.0.1:8001")
app = FastAPI(title="gateway-service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def project_request(method: str, path: str, role: str, payload: Any = None) -> Any:
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.request(
            method,
            f"{PROJECT_SERVICE_URL}{path}",
            headers={"X-OpenKATE-Role": role},
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", "upstream error"))
    return response.json()


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "gateway-service", "status": "ready"}


@app.get("/api/v1/system/health")
async def system_health() -> Dict[str, Any]:
    services: List[Dict[str, str]] = [{"service": "gateway-service", "status": "ready"}]
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get(f"{PROJECT_SERVICE_URL}/health")
        services.append({"service": "project-service", "status": "ready" if response.is_success else "degraded"})
    except httpx.HTTPError:
        services.append({"service": "project-service", "status": "unavailable"})
    return {"status": "ready" if all(item["status"] == "ready" for item in services) else "degraded", "services": services}


@app.get("/api/v1/workspaces")
async def list_workspaces(x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("GET", "/internal/v1/workspaces", x_openkate_role)


@app.get("/api/v1/workspaces/{workspace_id}/projects")
async def list_projects(workspace_id: str, x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("GET", f"/internal/v1/workspaces/{workspace_id}/projects", x_openkate_role)


@app.post("/api/v1/workspaces/{workspace_id}/projects", status_code=201)
async def create_project(workspace_id: str, request: Request, x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("POST", f"/internal/v1/workspaces/{workspace_id}/projects", x_openkate_role, await request.json())


@app.post("/api/v1/projects/{project_id}/environments", status_code=201)
async def create_environment(project_id: str, request: Request, x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("POST", f"/internal/v1/projects/{project_id}/environments", x_openkate_role, await request.json())


@app.get("/api/v1/projects/{project_id}/members")
async def list_members(project_id: str, x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("GET", f"/internal/v1/projects/{project_id}/members", x_openkate_role)


@app.get("/api/v1/projects/{project_id}/audit-logs")
async def list_audit_logs(project_id: str, x_openkate_role: str = Header(default="viewer")) -> Any:
    return await project_request("GET", f"/internal/v1/projects/{project_id}/audit-logs", x_openkate_role)

