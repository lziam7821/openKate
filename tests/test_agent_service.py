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


def test_knowledge_lookup_is_project_scoped_and_snapshot_is_stable() -> None:
    agent_service.knowledge.clear()
    agent_service.knowledge["project-a"] = [{"id": "knowledge-a", "projectId": "project-a", "title": "Payment timeout", "content": "Retry payment callback", "source": "incident-1"}]
    agent_service.knowledge["project-b"] = [{"id": "knowledge-b", "projectId": "project-b", "title": "Payment timeout", "content": "Other tenant", "source": "incident-2"}]
    result = asyncio.run(agent_service.list_knowledge("project-a", "payment callback"))
    repeated = asyncio.run(agent_service.list_knowledge("project-a", "payment callback"))
    assert result["snapshot"]["projectId"] == "project-a"
    assert result["snapshot"]["ids"] == ["knowledge-a"]
    assert result["snapshot"]["id"] == repeated["snapshot"]["id"]


def test_generation_injects_project_knowledge_snapshot_and_published_rules(monkeypatch) -> None:
    async def assets(_: list[str]):
        return [{"parse": {"text": "# Payment\nRetry callback", "citations": [{"source": "asset", "line": 1}]}}]

    async def rules(_: str):
        return [{"id": "rule-payment", "activeVersion": 1, "content": "Verify callback retries."}]

    agent_service.tasks.clear()
    agent_service.knowledge.clear()
    agent_service.knowledge["project-a"] = [{"id": "knowledge-a", "projectId": "project-a", "title": "Payment retry", "content": "Retry callback after timeout", "source": "incident-1"}]
    monkeypatch.setattr(agent_service, "parsed_assets", assets)
    monkeypatch.setattr(agent_service, "published_rules", rules)
    task = asyncio.run(agent_service.create_generation("project-a", agent_service.GenerationCreate(assetIds=["asset-1"])))
    assert task["draft"]["knowledgeSnapshot"]["ids"] == ["knowledge-a"]
    assert task["draft"]["ruleRefs"] == [{"id": "rule-payment", "activeVersion": 1, "content": "Verify callback retries."}]
