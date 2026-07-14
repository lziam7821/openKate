import asyncio
import importlib.util
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from fastapi import HTTPException

from openkate_executor import ExecutorRequest


ROOT = Path(__file__).parents[1]


def load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_executor = load("api_executor", "workers/executor-api/app/main.py")
state_executor = load("state_executor", "workers/executor-state/app/main.py")
ui_executor = load("ui_executor", "workers/executor-ui/app/main.py")


def test_api_executor_calls_real_http_transport_transfers_variable_and_redacts_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orders/order-42/pay"
        return httpx.Response(200, json={"status": "PAID", "token": "server-secret"})

    request = ExecutorRequest.model_validate(
        {
            "runId": "run-1",
            "stepId": "pay",
            "action": "request",
            "allowedHosts": ["payments.test"],
            "variables": {"orderId": "order-42", "accessToken": "client-secret"},
            "input": {
                "url": "https://payments.test/orders/{{ orderId }}/pay",
                "method": "POST",
                "headers": {"Authorization": "Bearer {{ accessToken }}"},
                "json": {"orderId": "{{ orderId }}"},
                "assertions": [{"path": "body.status", "operator": "equals", "expected": "PAID"}],
            },
        }
    )
    result = asyncio.run(api_executor.execute_api(request, httpx.MockTransport(handler)))
    assert result.output["body"]["status"] == "PAID"
    assert result.input_summary["headers"]["Authorization"] == "***"
    assert result.output_summary["body"]["token"] == "***"
    assert result.assertions[0]["passed"] is True


def test_api_and_ui_executors_enforce_project_allowlist() -> None:
    request = ExecutorRequest.model_validate({"runId": "run-1", "stepId": "step", "action": "request", "allowedHosts": ["allowed.test"], "input": {"url": "https://blocked.test"}})
    with pytest.raises(HTTPException) as api_error:
        asyncio.run(api_executor.execute_api(request, httpx.MockTransport(lambda _: httpx.Response(200))))
    assert api_error.value.status_code == 403
    with pytest.raises(HTTPException) as ui_error:
        asyncio.run(ui_executor.execute_ui(request))
    assert ui_error.value.status_code == 403


class CheckoutHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<!doctype html><input id="sku"><button id="submit" type="button" onclick="document.querySelector('#result').dataset.orderId='order-42';document.querySelector('#result').textContent='Created'">Order</button><div id="result"></div>'''
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return None


def test_ui_executor_uses_isolated_real_browser_and_saves_evidence(monkeypatch, tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENKATE_PLAYWRIGHT_CHANNEL", "chrome")
    monkeypatch.setenv("OPENKATE_ARTIFACT_DIR", str(tmp_path))
    request = ExecutorRequest.model_validate(
        {
            "runId": "run-ui",
            "stepId": "checkout",
            "action": "sequence",
            "allowedHosts": ["127.0.0.1"],
            "input": {
                "url": f"http://127.0.0.1:{server.server_port}",
                "actions": [
                    {"type": "fill", "selector": "#sku", "value": "SKU-1"},
                    {"type": "click", "selector": "#submit"},
                    {"type": "waitFor", "selector": "#result"},
                    {"type": "extractAttribute", "selector": "#result", "attribute": "data-order-id", "saveAs": "orderId"},
                ],
            },
        }
    )
    try:
        result = asyncio.run(ui_executor.execute_ui(request))
    finally:
        server.shutdown()
        server.server_close()
    assert result.output == {"orderId": "order-42"}
    assert all(Path(path).is_file() for path in result.evidence_refs)
    assert result.environment["browserContext"] == "run-ui:checkout"


class FakeCursor:
    description = [type("Column", (), {"name": "status"})()]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params):
        assert query.startswith("SELECT")
        assert params == {"order_id": "order-42"}

    def fetchall(self):
        return [("PAID",)]


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return FakeCursor()


def test_state_executor_is_parameterized_read_only_and_never_returns_credentials(monkeypatch) -> None:
    monkeypatch.setenv("OPENKATE_SECRET_STAGING_DB", "postgresql://user:password@db/openkate")
    request = ExecutorRequest.model_validate(
        {
            "runId": "run-1",
            "stepId": "verify",
            "action": "query",
            "variables": {"orderId": "order-42"},
            "input": {
                "connectionSecretRef": "staging-db",
                "query": "SELECT status FROM orders WHERE id = %(order_id)s",
                "params": {"order_id": "{{ orderId }}"},
                "assertions": [{"path": "rows.0.status", "operator": "equals", "expected": "PAID"}],
            },
        }
    )
    result = state_executor.execute_state(request, lambda dsn, options: FakeConnection())
    serialized = result.model_dump_json()
    assert result.output["rows"] == [{"status": "PAID"}]
    assert "postgresql://" not in serialized
    assert "password" not in serialized

    mutation = request.model_copy(update={"input": {**request.input, "query": "UPDATE orders SET status = 'PAID'"}})
    with pytest.raises(HTTPException) as error:
        state_executor.execute_state(mutation, lambda dsn, options: FakeConnection())
    assert error.value.status_code == 422
