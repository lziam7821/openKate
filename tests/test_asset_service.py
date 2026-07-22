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
    asset_service.evidence_assets.clear()
    client = TestClient(asset_service.app)
    created = client.post("/internal/v1/assets", json={"runId": "run-1", "stepId": "pay", "kind": "http", "contentType": "application/json", "contentBase64": "eyJzdGF0dXMiOiJQQUlEIn0="})
    assert created.status_code == 201
    assert created.json()["ref"].startswith("asset://asset_")
    asset_id = created.json()["id"]
    assert client.get(f"/internal/v1/assets/{asset_id}").content == b'{"status":"PAID"}'
    assert asset_service.evidence_assets[asset_id]["runId"] == "run-1"


def test_markdown_and_openapi_assets_keep_stable_source_locations(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_service, "ROOT", tmp_path)
    asset_service.documents.clear()
    client = TestClient(asset_service.app)
    created = client.post("/internal/v1/projects/project-1/assets", json={"name": "checkout.md", "contentType": "text/markdown", "contentBase64": "IyBDaGVja291dApQYXltZW50IG11c3Qgc3VjY2VlZC4="})
    parsed = client.post(f"/internal/v1/assets/{created.json()['id']}/parse")
    assert parsed.json()["citations"] == [{"source": "asset", "line": 1, "text": "# Checkout", "kind": "source"}, {"source": "asset", "line": 2, "text": "Payment must succeed.", "kind": "source"}]

    openapi = client.post("/internal/v1/projects/project-1/assets", json={"name": "payments.json", "contentType": "application/json", "contentBase64": "eyJwYXRocyI6eyIvcGF5bWVudHMiOnsicG9zdCI6e319fX0="})
    assert client.post(f"/internal/v1/assets/{openapi.json()['id']}/parse").json()["paths"] == [{"path": "/payments", "methods": ["post"]}]
