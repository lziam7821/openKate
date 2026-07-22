import base64
import os
from pathlib import Path

import httpx


def store_evidence(run_id: str, step_id: str, kind: str, content: bytes, content_type: str) -> str:
    service_url = os.getenv("OPENKATE_ASSET_SERVICE_URL")
    if not service_url:
        return f"run://{run_id}/steps/{step_id}/{kind}"
    try:
        response = httpx.post(
            f"{service_url}/internal/v1/assets",
            json={"runId": run_id, "stepId": step_id, "kind": kind, "contentType": content_type, "contentBase64": base64.b64encode(content).decode()},
            timeout=3.0,
        )
        response.raise_for_status()
        return response.json()["ref"]
    except httpx.HTTPError:
        return f"run://{run_id}/steps/{step_id}/{kind}"


def store_file_evidence(run_id: str, step_id: str, kind: str, path: Path, content_type: str) -> str:
    if not os.getenv("OPENKATE_ASSET_SERVICE_URL"):
        return str(path)
    return store_evidence(run_id, step_id, kind, path.read_bytes(), content_type)
