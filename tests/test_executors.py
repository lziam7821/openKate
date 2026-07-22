import asyncio
import importlib.util
import json
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
mobile_executor = load("mobile_executor", "workers/executor-mobile/app/main.py")
external_executor = load("external_executor", "workers/executor-external/app/main.py")
quality_executor = load("quality_executor", "workers/executor-quality/app/main.py")


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


def test_api_executor_runs_graphql_requests_and_asserts_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {"query": "query Product($sku: String!) { product(sku: $sku) { name } }", "variables": {"sku": "SKU-1"}, "operationName": None}
        return httpx.Response(200, json={"data": {"product": None}, "errors": [{"message": "not found"}]})

    request = ExecutorRequest.model_validate({"runId": "run-graphql", "stepId": "product", "action": "graphql", "allowedHosts": ["catalog.test"], "input": {"url": "https://catalog.test/graphql", "query": "query Product($sku: String!) { product(sku: $sku) { name } }", "variables": {"sku": "{{ sku }}"}, "assertions": [{"path": "body.errors.0.message", "operator": "equals", "expected": "not found"}]}, "variables": {"sku": "SKU-1"}})
    result = asyncio.run(api_executor.execute_api(request, httpx.MockTransport(handler)))
    assert result.environment["executor"] == "api.graphql"
    assert result.assertions[0]["passed"] is True


def test_api_executor_runs_allowlisted_grpc_unary_call() -> None:
    class Channel:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def unary_unary(self, method):
            assert method == "/payments.Payment/Authorize"

            def invoke(body, timeout, metadata):
                assert body == b"request"
                assert ("trace-id", "trace-1") in metadata
                return b"response"

            return invoke

    request = ExecutorRequest.model_validate({"runId": "run-grpc", "stepId": "authorize", "action": "grpc", "allowedHosts": ["payments.test"], "input": {"target": "payments.test:443", "method": "/payments.Payment/Authorize", "requestBase64": "cmVxdWVzdA==", "metadata": {"trace-id": "trace-1"}, "assertions": [{"path": "responseBase64", "operator": "equals", "expected": "cmVzcG9uc2U="}]}})
    result = asyncio.run(api_executor.execute_api(request, grpc_channel_factory=lambda target: Channel()))
    assert result.environment["executor"] == "api.grpc"


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


def test_state_executor_polls_until_eventually_consistent(monkeypatch) -> None:
    class Cursor:
        description = [type("Column", (), {"name": "status"})()]

        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params):
            assert query == "SELECT status FROM orders WHERE id = %(id)s"
            assert params == {"id": "order-42"}

        def fetchall(self):
            return [(self.status,)]

    statuses = iter(["PENDING", "PAID"])

    class Connection:
        def __init__(self):
            self.status = next(statuses)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor(self.status)

    monkeypatch.setenv("OPENKATE_SECRET_STAGING_DB", "postgresql://user:password@db/openkate")
    request = ExecutorRequest.model_validate({"runId": "run-wait", "stepId": "wait-order", "action": "wait", "timeoutMs": 1000, "input": {"connectionSecretRef": "staging-db", "query": "SELECT status FROM orders WHERE id = %(id)s", "params": {"id": "order-42"}, "pollIntervalMs": 1, "backoffMultiplier": 2, "assertions": [{"path": "rows.0.status", "operator": "equals", "expected": "PAID"}]}})
    result = state_executor.execute_state(request, lambda *args, **kwargs: Connection(), lambda _: None)
    assert result.output["rows"] == [{"status": "PAID"}]
    assert result.environment["polling"] is True


def test_state_executor_queries_log_and_trace_providers_with_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "observability.test"
        assert request.url.params["traceId"] == "trace-1"
        return httpx.Response(200, json={"spans": [{"name": "checkout", "status": "OK"}]})

    request = ExecutorRequest.model_validate({"runId": "run-trace", "stepId": "trace", "action": "trace", "allowedHosts": ["observability.test"], "input": {"url": "https://observability.test/traces", "params": {"traceId": "trace-1"}, "assertions": [{"path": "spans.0.status", "operator": "equals", "expected": "OK"}]}})
    result = state_executor.execute_state(request, http_transport=httpx.MockTransport(handler))
    assert result.environment["executor"] == "state.trace"


def test_state_executor_reads_redis_value_and_ttl(monkeypatch) -> None:
    class Cache:
        def get(self, key):
            assert key == "order:42"
            return b"PAID"

        def ttl(self, key):
            return 300

    monkeypatch.setenv("OPENKATE_SECRET_CACHE", "redis://cache.test/0")
    request = ExecutorRequest.model_validate({"runId": "run-cache", "stepId": "cache", "action": "cache", "input": {"connectionSecretRef": "cache", "key": "order:42", "assertions": [{"path": "value", "operator": "equals", "expected": "PAID"}, {"path": "ttl", "operator": "equals", "expected": 300}]}})
    result = state_executor.execute_state(request, cache_factory=lambda url: Cache())
    assert result.environment["executor"] == "state.redis.read_only"


def test_mobile_executor_collects_screenshot_and_page_source_with_device_actions(monkeypatch, tmp_path) -> None:
    class Element:
        text = "Order created"

        def clear(self):
            return None

        def send_keys(self, value):
            assert value == "SKU-1"

        def click(self):
            return None

    class Driver:
        page_source = "<hierarchy/>"

        def find_element(self, by, selector):
            assert by == "id"
            assert selector in {"sku", "submit", "result"}
            return Element()

        def get_screenshot_as_png(self):
            return b"png"

        def quit(self):
            return None

    monkeypatch.setenv("OPENKATE_ARTIFACT_DIR", str(tmp_path))
    request = ExecutorRequest.model_validate({"runId": "run-mobile", "stepId": "checkout", "action": "sequence", "input": {"deviceId": "emulator-1", "capabilities": {"appium:deviceName": "Pixel", "appium:udid": "emulator-1"}, "actions": [{"type": "fill", "by": "id", "selector": "sku", "value": "SKU-1"}, {"type": "tap", "by": "id", "selector": "submit"}, {"type": "extractText", "by": "id", "selector": "result", "saveAs": "message"}]}})
    result = asyncio.run(mobile_executor.execute_mobile(request, lambda _: Driver()))
    assert result.output == {"message": "Order created"}
    assert result.environment["device"] == "Pixel"
    assert all(Path(path).is_file() for path in result.evidence_refs)


def test_mobile_executor_is_unavailable_without_appium_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("OPENKATE_APPIUM_URL", raising=False)
    assert asyncio.run(mobile_executor.health())["status"] == "unavailable"


def test_external_executor_waits_for_callback_and_exposes_test_data() -> None:
    external_executor.store.callbacks.clear()
    external_executor.store.receive("payment-42", {"status": "PAID", "reference": "pay-42"})
    callback = ExecutorRequest.model_validate({"runId": "run-external", "stepId": "callback", "action": "waitForCallback", "input": {"callbackToken": "payment-42", "assertions": [{"path": "callbacks.0.payload.status", "operator": "equals", "expected": "PAID"}]}})
    result = asyncio.run(external_executor.execute_external(callback))
    assert result.output["callbacks"][0]["payload"]["reference"] == "pay-42"

    data = ExecutorRequest.model_validate({"runId": "run-external", "stepId": "data", "action": "data", "input": {"data": {"sku": "SKU-1"}}})
    assert asyncio.run(external_executor.execute_external(data)).output == {"sku": "SKU-1"}


def test_quality_executor_parses_k6_summary_and_enforces_script_directory(monkeypatch, tmp_path) -> None:
    script = tmp_path / "checkout.js"
    script.write_text("export default function () {}")
    monkeypatch.setenv("OPENKATE_QUALITY_SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(quality_executor, "binary", lambda _: "k6")

    def run(command, **kwargs):
        Path(command[3]).write_text(json.dumps({"metrics": {"http_req_duration": {"avg": 120}}}))
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    request = ExecutorRequest.model_validate({"runId": "run-quality", "stepId": "load", "action": "k6", "input": {"script": "checkout.js", "assertions": [{"path": "metrics.http_req_duration.avg", "operator": "equals", "expected": 120}]}})
    result = quality_executor.execute_quality(request, run)
    assert result.environment["executor"] == "quality.k6"
