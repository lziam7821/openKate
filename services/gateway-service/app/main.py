import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

PROJECT_SERVICE_URL = os.getenv("OPENKATE_PROJECT_SERVICE_URL", "http://127.0.0.1:8001")
VALIDATION_SERVICE_URL = os.getenv("OPENKATE_VALIDATION_SERVICE_URL", "http://127.0.0.1:8002")
REPORT_SERVICE_URL = os.getenv("OPENKATE_REPORT_SERVICE_URL", "http://127.0.0.1:8003")

app = FastAPI(title="gateway-service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def upstream(url: str, method: str, path: str, request: Request, payload: Any = None) -> httpx.Response:
    headers = {"X-OpenKATE-Role": request.headers.get("X-OpenKATE-Role", "viewer"), "X-OpenKATE-Actor": request.headers.get("X-OpenKATE-Actor", "local-user")}
    if if_match := request.headers.get("If-Match"):
        headers["If-Match"] = if_match
    async with httpx.AsyncClient(timeout=3.0) as client:
        return await client.request(method, f"{url}{path}", headers=headers, json=payload, params=request.query_params)


def proxy_error(response: httpx.Response) -> JSONResponse:
    try:
        detail = response.json().get("detail", "upstream error")
    except ValueError:
        detail = "upstream error"
    if isinstance(detail, dict):
        code, message = detail.get("code", "UPSTREAM_ERROR"), detail.get("message", "upstream error")
    else:
        code, message = "UPSTREAM_ERROR", str(detail)
    return JSONResponse(status_code=response.status_code, content={"error": {"code": code, "message": message, "requestId": str(uuid4()), "details": {}}})


def proxy_success(response: httpx.Response, extra_headers: Optional[Dict[str, str]] = None) -> JSONResponse:
    headers = extra_headers or {}
    if etag := response.headers.get("etag"):
        headers["ETag"] = etag
    return JSONResponse(status_code=response.status_code, content=response.json(), headers=headers)


async def project_request(method: str, path: str, request: Request, payload: Any = None) -> JSONResponse:
    try:
        response = await upstream(PROJECT_SERVICE_URL, method, path, request, payload)
    except httpx.HTTPError:
        return JSONResponse(status_code=503, content={"error": {"code": "PROJECT_SERVICE_UNAVAILABLE", "message": "project service unavailable", "requestId": str(uuid4()), "details": {}}})
    return proxy_error(response) if response.is_error else proxy_success(response)


async def index_scenario(scenario: Dict[str, Any]) -> None:
    event = {
        "eventId": str(uuid4()),
        "eventType": "validation.scenario.projected.v1",
        "projectId": scenario["projectId"],
        "aggregateId": scenario["id"],
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "payload": {"scenario": scenario},
    }
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.post(f"{REPORT_SERVICE_URL}/internal/v1/events", json=event)
    except httpx.HTTPError:
        pass


async def scenario_write(method: str, path: str, request: Request, payload: Any = None) -> JSONResponse:
    try:
        response = await upstream(VALIDATION_SERVICE_URL, method, path, request, payload)
    except httpx.HTTPError:
        return JSONResponse(status_code=503, content={"error": {"code": "VALIDATION_SERVICE_UNAVAILABLE", "message": "validation service unavailable", "requestId": str(uuid4()), "details": {}}})
    if response.is_error:
        return proxy_error(response)
    scenario = response.json()
    await index_scenario(scenario)
    return proxy_success(response)


@app.get("/health", tags=["system"])
async def health() -> Dict[str, str]:
    return {"service": "gateway-service", "status": "ready"}


@app.get("/api/v1/system/health")
async def system_health() -> Dict[str, Any]:
    services: List[Dict[str, str]] = [{"service": "gateway-service", "status": "ready"}]
    async with httpx.AsyncClient(timeout=1.0) as client:
        for service, url in (("project-service", PROJECT_SERVICE_URL), ("validation-service", VALIDATION_SERVICE_URL), ("report-service", REPORT_SERVICE_URL)):
            try:
                response = await client.get(f"{url}/health")
                services.append({"service": service, "status": "ready" if response.is_success else "degraded"})
            except httpx.HTTPError:
                services.append({"service": service, "status": "unavailable"})
    return {"status": "ready" if all(item["status"] == "ready" for item in services) else "degraded", "services": services}


@app.get("/api/v1/workspaces")
async def list_workspaces(request: Request) -> JSONResponse:
    return await project_request("GET", "/internal/v1/workspaces", request)


@app.get("/api/v1/workspaces/{workspace_id}/projects")
async def list_projects(workspace_id: str, request: Request) -> JSONResponse:
    return await project_request("GET", f"/internal/v1/workspaces/{workspace_id}/projects", request)


@app.post("/api/v1/workspaces/{workspace_id}/projects", status_code=201)
async def create_project(workspace_id: str, request: Request) -> JSONResponse:
    return await project_request("POST", f"/internal/v1/workspaces/{workspace_id}/projects", request, await request.json())


@app.post("/api/v1/projects/{project_id}/environments", status_code=201)
async def create_environment(project_id: str, request: Request) -> JSONResponse:
    return await project_request("POST", f"/internal/v1/projects/{project_id}/environments", request, await request.json())


@app.get("/api/v1/projects/{project_id}/members")
async def list_members(project_id: str, request: Request) -> JSONResponse:
    return await project_request("GET", f"/internal/v1/projects/{project_id}/members", request)


@app.get("/api/v1/projects/{project_id}/audit-logs")
async def list_audit_logs(project_id: str, request: Request) -> JSONResponse:
    return await project_request("GET", f"/internal/v1/projects/{project_id}/audit-logs", request)


@app.post("/api/v1/projects/{project_id}/scenarios", status_code=201)
async def create_scenario(project_id: str, request: Request) -> JSONResponse:
    return await scenario_write("POST", f"/internal/v1/projects/{project_id}/scenarios", request, await request.json())


@app.get("/api/v1/projects/{project_id}/scenarios")
async def list_scenarios(project_id: str, request: Request) -> JSONResponse:
    try:
        response = await upstream(REPORT_SERVICE_URL, "GET", f"/internal/v1/projects/{project_id}/scenarios", request)
        if response.is_success:
            return proxy_success(response)
    except httpx.HTTPError:
        pass
    response = await upstream(VALIDATION_SERVICE_URL, "GET", f"/internal/v1/projects/{project_id}/scenarios", request)
    if response.is_error:
        return proxy_error(response)
    return proxy_success(response, {"X-OpenKATE-Read-Model": "degraded"})


@app.get("/api/v1/scenarios/{scenario_id}")
async def scenario_detail(scenario_id: str, request: Request) -> JSONResponse:
    response = await upstream(VALIDATION_SERVICE_URL, "GET", f"/internal/v1/scenarios/{scenario_id}", request)
    return proxy_error(response) if response.is_error else proxy_success(response)


@app.patch("/api/v1/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, request: Request) -> JSONResponse:
    return await scenario_write("PATCH", f"/internal/v1/scenarios/{scenario_id}", request, await request.json())


@app.post("/api/v1/scenarios/{scenario_id}/submit-review")
async def submit_review(scenario_id: str, request: Request) -> JSONResponse:
    return await scenario_write("POST", f"/internal/v1/scenarios/{scenario_id}/submit-review", request)


@app.post("/api/v1/scenarios/{scenario_id}/reviews", status_code=201)
async def create_review(scenario_id: str, request: Request) -> JSONResponse:
    return await scenario_write("POST", f"/internal/v1/scenarios/{scenario_id}/reviews", request, await request.json())


@app.post("/api/v1/scenarios/{scenario_id}/approve")
async def approve_scenario(scenario_id: str, request: Request) -> JSONResponse:
    return await scenario_write("POST", f"/internal/v1/scenarios/{scenario_id}/approve", request)


@app.post("/api/v1/scenarios/{scenario_id}/reject")
async def reject_scenario(scenario_id: str, request: Request) -> JSONResponse:
    return await scenario_write("POST", f"/internal/v1/scenarios/{scenario_id}/reject", request, await request.json())


@app.get("/api/v1/scenarios/{scenario_id}/versions")
async def list_versions(scenario_id: str, request: Request) -> JSONResponse:
    response = await upstream(VALIDATION_SERVICE_URL, "GET", f"/internal/v1/scenarios/{scenario_id}/versions", request)
    return proxy_error(response) if response.is_error else proxy_success(response)


@app.get("/api/v1/scenarios/{scenario_id}/diff")
async def scenario_diff(scenario_id: str, request: Request) -> JSONResponse:
    response = await upstream(VALIDATION_SERVICE_URL, "GET", f"/internal/v1/scenarios/{scenario_id}/diff", request)
    return proxy_error(response) if response.is_error else proxy_success(response)
