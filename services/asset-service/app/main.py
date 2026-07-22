import base64
import os
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from openkate_common.service_app import instrument_app

app = FastAPI(title="asset-service", version="0.3.0")
instrument_app(app, "asset-service", ["evidence"])
ROOT = Path(os.getenv("OPENKATE_ASSET_DIR", "/tmp/openkate-assets"))
documents: Dict[str, Dict] = {}


class AssetCreate(BaseModel):
    run_id: str = Field(alias="runId", min_length=1)
    step_id: str = Field(alias="stepId", min_length=1)
    kind: str = Field(min_length=1, max_length=100)
    content_type: str = Field(alias="contentType", min_length=1, max_length=200)
    content_base64: str = Field(alias="contentBase64", min_length=1)


class DocumentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    content_type: str = Field(alias="contentType", min_length=1, max_length=200)
    content_base64: str = Field(alias="contentBase64", min_length=1)


def decode(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="asset content is not valid base64") from error


def citations(text: str) -> List[Dict]:
    return [{"source": "asset", "line": index, "text": line, "kind": "source"} for index, line in enumerate(text.splitlines(), 1) if line.strip()]


@app.post("/internal/v1/assets", status_code=201)
async def create_asset(payload: AssetCreate) -> dict:
    content = decode(payload.content_base64)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="asset exceeds 20 MiB limit")
    asset_id = f"asset_{uuid4().hex}"
    path = ROOT / payload.run_id / payload.step_id / asset_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"id": asset_id, "ref": f"asset://{asset_id}", "contentType": payload.content_type, "path": str(path)}


@app.post("/internal/v1/projects/{project_id}/assets", status_code=201)
async def import_document(project_id: str, payload: DocumentCreate) -> Dict:
    content = decode(payload.content_base64)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="asset exceeds 20 MiB limit")
    asset_id = f"asset_{uuid4().hex}"
    path = ROOT / "documents" / asset_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    documents[asset_id] = {"id": asset_id, "projectId": project_id, "name": payload.name, "contentType": payload.content_type, "path": str(path), "parse": None}
    return {key: value for key, value in documents[asset_id].items() if key != "path"}


@app.post("/internal/v1/assets/{asset_id}/parse")
async def parse_document(asset_id: str) -> Dict:
    document = documents.get(asset_id)
    if document is None:
        raise HTTPException(status_code=404, detail="asset not found")
    content = Path(document["path"]).read_bytes()
    if document["contentType"] in {"application/json", "application/vnd.oai.openapi+json"} or document["name"].lower().endswith((".json", ".yaml", ".yml")):
        import json
        try:
            parsed = json.loads(content)
            paths = [{"path": path, "methods": sorted(value.keys())} for path, value in parsed.get("paths", {}).items()] if isinstance(parsed, dict) else []
            result = {"kind": "openapi", "paths": paths, "citations": [{"source": "asset", "path": path["path"], "kind": "source"} for path in paths]}
        except (UnicodeDecodeError, json.JSONDecodeError):
            result = {"kind": "openapi", "paths": [], "citations": [], "parseStatus": "invalid"}
    elif document["contentType"] == "application/pdf" or document["name"].lower().endswith(".pdf"):
        result = {"kind": "pdf", "citations": [], "parseStatus": "unsupported"}
    else:
        text = content.decode("utf-8", errors="replace")
        result = {"kind": "markdown", "text": text, "citations": citations(text), "parseStatus": "completed"}
    document["parse"] = result
    return {"assetId": asset_id, **result}


@app.get("/internal/v1/assets/{asset_id}/document")
async def document_detail(asset_id: str) -> Dict:
    document = documents.get(asset_id)
    if document is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return {key: value for key, value in document.items() if key != "path"}


@app.get("/internal/v1/assets/{asset_id}")
async def read_asset(asset_id: str) -> FileResponse:
    matches = list(ROOT.glob(f"*/*/{asset_id}"))
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(matches[0])
