import os
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

from openkate_executor import CONTRACT_VERSION, SDK_VERSION, ExecutorRequest, ExecutorResult, assert_allowed_url, redact, render_templates, store_file_evidence
from openkate_common.service_app import instrument_app

app = FastAPI(title="executor-ui", version="0.3.0")
instrument_app(app, "executor-ui", ["ui.playwright"])


async def execute_ui(request: ExecutorRequest) -> ExecutorResult:
    payload = render_templates(request.input, request.variables)
    url = str(payload.get("url", ""))
    assert_allowed_url(url, request.allowed_hosts)
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise HTTPException(status_code=503, detail="Playwright executor dependency is unavailable") from error

    artifact_root = Path(os.getenv("OPENKATE_ARTIFACT_DIR", "/tmp/openkate-artifacts")) / request.run_id / request.step_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    screenshot = artifact_root / "screenshot.png"
    trace = artifact_root / "trace.zip"
    output: Dict[str, Any] = {}
    async with async_playwright() as playwright:
        channel = os.getenv("OPENKATE_PLAYWRIGHT_CHANNEL")
        browser = await playwright.chromium.launch(headless=True, channel=channel)
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=request.timeout_ms)
            for action in payload.get("actions", []):
                action_type = action.get("type")
                selector = action.get("selector")
                if action_type == "fill":
                    await page.locator(selector).fill(str(action.get("value", "")))
                elif action_type == "click":
                    await page.locator(selector).click()
                elif action_type == "waitFor":
                    await page.locator(selector).wait_for()
                elif action_type == "extractText":
                    output[str(action["saveAs"])] = await page.locator(selector).inner_text()
                elif action_type == "extractAttribute":
                    output[str(action["saveAs"])] = await page.locator(selector).get_attribute(str(action["attribute"]))
                else:
                    raise HTTPException(status_code=422, detail=f"unsupported UI action: {action_type}")
            await page.screenshot(path=str(screenshot), full_page=True)
        finally:
            await context.tracing.stop(path=str(trace))
            await context.close()
            await browser.close()
    return ExecutorResult(
        status="completed",
        output=output,
        inputSummary=redact({"url": url, "actions": payload.get("actions", [])}),
        outputSummary=redact(output),
        evidenceRefs=[store_file_evidence(request.run_id, request.step_id, "screenshot", screenshot, "image/png"), store_file_evidence(request.run_id, request.step_id, "trace", trace, "application/zip")],
        environment={"executor": "ui.playwright", "browserContext": f"{request.run_id}:{request.step_id}"},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"worker": "executor-ui", "status": "ready", "capabilities": ["ui.web"], "sdkVersion": SDK_VERSION, "contractVersion": CONTRACT_VERSION}


@app.post("/execute", response_model=ExecutorResult)
async def execute(request: ExecutorRequest) -> ExecutorResult:
    return await execute_ui(request)
