import json
import logging
import os
import time
from collections import Counter
from typing import Dict, Iterable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse


def instrument_app(app: FastAPI, service_name: str, capabilities: Iterable[str] = ()) -> None:
    requests: Counter[tuple[str, str, int]] = Counter()
    logger = logging.getLogger(service_name)
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_counter = None
    if otel_endpoint:
        from opentelemetry import metrics as otel_metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otel_endpoint}/v1/traces")))
        trace.set_tracer_provider(tracer_provider)
        metric_provider = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{otel_endpoint}/v1/metrics"))])
        otel_metrics.set_meter_provider(metric_provider)
        otel_counter = otel_metrics.get_meter(service_name).create_counter("openkate.http.requests")
        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.monotonic()
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        requests[(request.method, request.url.path, response.status_code)] += 1
        if otel_counter:
            otel_counter.add(1, {"method": request.method, "path": request.url.path, "status": response.status_code})
        response.headers.setdefault("X-Request-ID", request_id)
        logger.info(json.dumps({"service": service_name, "requestId": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "durationMs": round((time.monotonic() - started) * 1000, 2)}))
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        lines = [f'openkate_http_requests_total{{service="{service_name}",method="{method}",path="{path}",status="{status}"}} {count}' for (method, path, status), count in sorted(requests.items())]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/capabilities", tags=["system"])
    async def capability_registration() -> Dict[str, object]:
        return {"service": service_name, "capabilities": list(capabilities)}


def create_service_app(service_name: str) -> FastAPI:
    app = FastAPI(title=service_name, version="0.1.0")
    instrument_app(app, service_name)

    @app.get("/health", tags=["system"])
    async def health() -> Dict[str, str]:
        return {"service": service_name, "status": "ready"}

    return app
