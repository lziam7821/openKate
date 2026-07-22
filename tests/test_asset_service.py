import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).parents[1] / "services" / "asset-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("asset_service", MODULE_PATH)
assert spec and spec.loader
asset_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asset_service)


def test_asset_service_stores_and_serves_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_service, "ROOT", tmp_path)
    client = TestClient(asset_service.app)
    created = client.post("/internal/v1/assets", json={"runId": "run-1", "stepId": "pay", "kind": "http", "contentType": "application/json", "contentBase64": "eyJzdGF0dXMiOiJQQUlEIn0="})
    assert created.status_code == 201
    assert created.json()["ref"].startswith("asset://asset_")
    asset_id = created.json()["id"]
    assert client.get(f"/internal/v1/assets/{asset_id}").content == b'{"status":"PAID"}'
