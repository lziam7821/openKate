import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException

from openkate_common.service_app import instrument_app
from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, ExecutorRuntime, assert_allowed_url, evaluate_assertions, redact, render_templates, store_evidence

app = FastAPI(title="executor-quality", version="0.8.0")
instrument_app(app, "executor-quality", ["quality.k6", "quality.zap"])


def binary(name: str) -> Optional[str]:
    return shutil.which(os.getenv(f"OPENKATE_{name.upper()}_BIN", name))


def quality_script(path: str) -> Path:
    root = Path(os.getenv("OPENKATE_QUALITY_SCRIPT_DIR", "/quality-scripts")).resolve()
    candidate = (root / path).resolve()
    if root not in candidate.parents or candidate.suffix != ".js":
        raise HTTPException(status_code=422, detail="quality script must be a JavaScript file inside the quality script directory")
    return candidate


def execute_quality(request: ExecutorRequest, run: Callable[..., Any] = subprocess.run) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    with TemporaryDirectory() as directory:
        report = Path(directory) / "report.json"
        if request.action == "k6":
            executable = binary("k6")
            if not executable:
                raise HTTPException(status_code=503, detail="k6 executable is unavailable")
            script = quality_script(str(payload.get("script", "")))
            command = [executable, "run", "--summary-export", str(report), str(script)]
        elif request.action == "zap":
            executable = binary("zap")
            target = str(payload.get("target", ""))
            assert_allowed_url(target, request.allowed_hosts)
            command = [executable, "-t", target, "-J", str(report)]
        else:
            raise HTTPException(status_code=422, detail=f"unsupported quality action: {request.action}")
        completed = run(command, capture_output=True, text=True, timeout=request.timeout_ms / 1000)
        if completed.returncode != 0:
            raise HTTPException(status_code=422, detail=f"quality tool failed: {completed.stderr[-500:]}")
        result = json.loads(report.read_text()) if report.exists() else {"stdout": completed.stdout}
    assertions = evaluate_assertions(result, payload.get("assertions", []))
    if any(not item["passed"] for item in assertions):
        raise HTTPException(status_code=422, detail="quality assertion failed")
    return ExecutorResult(status="completed", output=result, inputSummary=redact({"action": request.action, "input": payload}), outputSummary=redact(result), assertions=assertions, evidenceRefs=[store_evidence(request.run_id, request.step_id, request.action, json.dumps(redact(result)).encode(), "application/json")], environment={"executor": f"quality.{request.action}"})


executor = ExecutorRuntime(["quality.k6", "quality.zap"], lambda request: asyncio.to_thread(execute_quality, request))


@app.get("/health")
async def health() -> Dict[str, Any]:
    capabilities = [f"quality.{name}" for name in ("k6", "zap") if binary(name)]
    return {"worker": "executor-quality", "status": "ready" if capabilities else "unavailable", "capabilities": capabilities or ["quality.unavailable"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await executor.execute(request)


@app.post("/cancel")
async def cancel(request: ExecutorRequest) -> Dict[str, str]:
    await executor.cancel(request)
    return {"runId": request.run_id, "stepId": request.step_id, "status": "canceling"}
