import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException

from openkate_common.service_app import instrument_app
from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, redact, render_templates, store_file_evidence

app = FastAPI(title="executor-mobile", version="0.8.0")
instrument_app(app, "executor-mobile", ["mobile.appium"])


def appium_url() -> str:
    return os.getenv("OPENKATE_APPIUM_URL", "").strip()


def create_driver(capabilities: Dict[str, Any]) -> Any:
    if not (url := appium_url()):
        raise HTTPException(status_code=503, detail="Appium endpoint is not configured")
    try:
        from appium import webdriver
        from appium.options.common.base import AppiumOptions
    except ImportError as error:
        raise HTTPException(status_code=503, detail="Appium executor dependency is unavailable") from error
    options = AppiumOptions()
    options.load_capabilities(capabilities)
    return webdriver.Remote(url, options=options)


async def execute_mobile(request: ExecutorRequest, driver_factory: Optional[Callable[[Dict[str, Any]], Any]] = None) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise HTTPException(status_code=422, detail="mobile capabilities are required")
    driver = await asyncio.to_thread(driver_factory or create_driver, capabilities)
    artifact_root = Path(os.getenv("OPENKATE_ARTIFACT_DIR", "/tmp/openkate-artifacts")) / request.run_id / request.step_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    screenshot = artifact_root / "screenshot.png"
    page_source = artifact_root / "page-source.xml"
    output: Dict[str, Any] = {}
    try:
        for action in payload.get("actions", []):
            action_type = action.get("type")
            if action_type == "tap":
                await asyncio.to_thread(lambda: driver.find_element(action["by"], action["selector"]).click())
            elif action_type == "fill":
                element = await asyncio.to_thread(driver.find_element, action["by"], action["selector"])
                await asyncio.to_thread(element.clear)
                await asyncio.to_thread(element.send_keys, str(action.get("value", "")))
            elif action_type == "extractText":
                element = await asyncio.to_thread(driver.find_element, action["by"], action["selector"])
                output[str(action["saveAs"])] = await asyncio.to_thread(lambda: element.text)
            else:
                raise HTTPException(status_code=422, detail=f"unsupported mobile action: {action_type}")
        screenshot.write_bytes(await asyncio.to_thread(driver.get_screenshot_as_png))
        page_source.write_text(await asyncio.to_thread(lambda: driver.page_source), encoding="utf-8")
    finally:
        await asyncio.to_thread(driver.quit)
    return ExecutorResult(
        status="completed",
        output=output,
        inputSummary=redact({"capabilities": capabilities, "actions": payload.get("actions", [])}),
        outputSummary=redact(output),
        evidenceRefs=[store_file_evidence(request.run_id, request.step_id, "screenshot", screenshot, "image/png"), store_file_evidence(request.run_id, request.step_id, "page-source", page_source, "application/xml")],
        environment={"executor": "mobile.appium", "device": capabilities.get("appium:deviceName") or capabilities.get("deviceName")},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-mobile", "status": "ready" if appium_url() else "unavailable", "capabilities": ["mobile.appium"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await execute_mobile(request)
