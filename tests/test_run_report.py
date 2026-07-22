import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "services" / "report-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("run_report_service", MODULE_PATH)
assert spec and spec.loader
report_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_service)


def context(status: str = "completed", evidence: bool = True, error=None) -> dict:
    return {"run": {"id": "run-1", "scenarioId": "scenario-1", "scenarioVersion": 1, "planId": "plan-1", "status": status, "createdAt": "2026-01-01T00:00:00Z", "stepResults": [{"stepId": "checkout", "status": "failed" if error else "completed", "evidenceRefs": ["asset://screen"] if evidence else [], "assertions": [], "error": error}]}, "plan": {"orderedStepIds": ["checkout"]}}


def test_outcome_is_deterministic_and_requires_evidence() -> None:
    assert report_service.report_for(context())["outcome"] == "passed"
    assert report_service.report_for(context(evidence=False))["outcome"] == "inconclusive"
    assert report_service.report_for(context(status="failed", error={"category": "environment", "message": "offline"}))["outcome"] == "blocked"
    assert report_service.report_for(context(status="failed", error={"category": "assertion", "message": "wrong status"}))["outcome"] == "failed"
