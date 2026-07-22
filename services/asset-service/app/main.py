import base64
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from openkate_common.service_app import instrument_app

app = FastAPI(title="asset-service", version="0.3.0")
instrument_app(app, "asset-service", ["evidence"])
ROOT = Path(os.getenv("OPENKATE_ASSET_DIR", "/tmp/openkate-assets"))


class AssetCreate(BaseModel):
    run_id: str = Field(alias="runId", min_length=1)
    step_id: str = Field(alias="stepId", min_length=1)
    kind: str = Field(min_length=1, max_length=100)
    content_type: str = Field(alias="contentType", min_length=1, max_length=200)
    content_base64: str = Field(alias="contentBase64", min_length=1)


@app.post("/internal/v1/assets", status_code=201)
async def create_asset(payload: AssetCreate) -> dict:
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="asset content is not valid base64") from error
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="asset exceeds 20 MiB limit")
    asset_id = f"asset_{uuid4().hex}"
    path = ROOT / payload.run_id / payload.step_id / asset_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"id": asset_id, "ref": f"asset://{asset_id}", "contentType": payload.content_type, "path": str(path)}


@app.get("/internal/v1/assets/{asset_id}")
async def read_asset(asset_id: str) -> FileResponse:
    matches = list(ROOT.glob(f"*/*/{asset_id}"))
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(matches[0])
