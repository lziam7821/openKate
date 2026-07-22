import importlib.util
import asyncio
from pathlib import Path

import httpx


MODULE_PATH = Path(__file__).parents[1] / "services" / "agent-service" / "app" / "main.py"
spec = importlib.util.spec_from_file_location("agent_service", MODULE_PATH)
assert spec and spec.loader
agent_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_service)


def test_generation_draft_is_source_grounded_and_marks_missing_sources_inferred() -> None:
    draft = agent_service.draft_from_assets("project-1", [{"parse": {"text": "# Checkout\nA paid order is created.", "citations": [{"source": "asset", "line": 1}]}}])
    assert draft["title"] == "Checkout"
    assert draft["businessGoal"] == "A paid order is created."
    assert draft["citations"] == [{"source": "asset", "line": 1}]

    inferred = agent_service.draft_from_assets("project-1", [{"parse": {}}])
    assert inferred["quality"] == 0.5
    assert inferred["citations"] == [{"source": "inferred", "kind": "inferred"}]


def test_accepting_a_reviewed_generation_imports_a_draft_scenario(monkeypatch) -> None:
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, headers, json):
            assert headers["X-OpenKATE-Role"] == "developer"
            assert json["actors"] == ["qa"]
            return httpx.Response(201, json={"id": "scenario-generated"})

    agent_service.tasks.clear()
    agent_service.tasks["generation-1"] = {"id": "generation-1", "projectId": "project-1", "status": "needs_review", "draft": agent_service.draft_from_assets("project-1", [{"parse": {"text": "# Checkout\nOrder succeeds", "citations": [{"source": "asset", "line": 1}]}}]), "events": [], "review": None}
    monkeypatch.setattr(agent_service.httpx, "AsyncClient", lambda **_: Client())
    accepted = asyncio.run(agent_service.accept_generation("generation-1"))
    assert accepted["status"] == "accepted"
    assert accepted["scenarioId"] == "scenario-generated"
