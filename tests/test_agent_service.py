import importlib.util
from pathlib import Path


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
