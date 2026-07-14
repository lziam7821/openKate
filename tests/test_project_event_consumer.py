import asyncio
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "services" / "governance-service" / "app" / "consumer.py"
spec = importlib.util.spec_from_file_location("project_event_consumer", MODULE_PATH)
assert spec and spec.loader
project_event_consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project_event_consumer)


def test_project_event_consumer_records_duplicate_event_once() -> None:
    consumer = project_event_consumer.ProjectEventConsumer()
    event = {"eventId": "event-1", "eventType": "project.created.v1", "projectId": "project-1", "payload": {"name": "Checkout"}}
    asyncio.run(consumer.handle(event))
    asyncio.run(consumer.handle(event))
    assert consumer.receipts == {"event-1"}
