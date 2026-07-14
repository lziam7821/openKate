import asyncio

from openkate_common.messaging import IdempotentConsumer, consume_jetstream_message, publish_pending

from app.main import store
from test_project_service import client


def test_project_outbox_publishes_all_contracts_and_consumer_is_idempotent() -> None:
    store.outbox_events.clear()
    owner = {"X-OpenKATE-Role": "owner", "X-OpenKATE-Actor": "owner-events"}
    project = client.post("/internal/v1/workspaces/workspace_demo/projects", headers=owner, json={"name": "Events"}).json()
    project_id = project["id"]
    client.patch(f"/internal/v1/projects/{project_id}", headers=owner, json={"description": "Updated"})
    environment = client.post(f"/internal/v1/projects/{project_id}/environments", headers=owner, json={"name": "Staging", "base_url": "https://events.test"}).json()
    client.patch(f"/internal/v1/projects/{project_id}/environments/{environment['id']}", headers=owner, json={"write_policy": "read_only"})
    client.post(f"/internal/v1/projects/{project_id}/members", headers=owner, json={"user_id": "viewer-events", "role": "viewer"})
    events = store.pending_events()
    assert {event["eventType"] for event in events} == {
        "project.created.v1", "project.updated.v1", "project.environment.created.v1",
        "project.policy.changed.v1", "project.member.changed.v1",
    }

    published: list[tuple[str, dict]] = []

    async def publish(subject: str, event: dict) -> None:
        published.append((subject, event))

    assert asyncio.run(publish_pending(events, publish, store.mark_published)) == len(events)
    assert store.pending_events() == []
    assert all(subject.startswith("openkate.project.") for subject, _ in published)

    effects: list[str] = []
    consumer = IdempotentConsumer()
    event = published[0][1]
    assert consumer.consume(event, lambda item: effects.append(item["eventId"])) is True
    assert consumer.consume(event, lambda item: effects.append(item["eventId"])) is False
    assert effects == [event["eventId"]]


def test_jetstream_consumer_retries_then_sends_dead_letter() -> None:
    class Metadata:
        num_delivered = 2

    class Message:
        data = b'{"eventId":"failed-event"}'
        subject = "openkate.project.created.v1"
        metadata = Metadata()
        acked = False
        delay = 0

        async def ack(self) -> None:
            self.acked = True

        async def nak(self, delay: int) -> None:
            self.delay = delay

    async def fail(event: dict) -> None:
        raise RuntimeError(event["eventId"])

    dead_letters: list[tuple[str, bytes]] = []

    async def dead_letter(subject: str, data: bytes) -> None:
        dead_letters.append((subject, data))

    retry = Message()
    asyncio.run(consume_jetstream_message(retry, fail, dead_letter, max_deliver=3))
    assert retry.delay == 4
    assert retry.acked is False

    retry.metadata.num_delivered = 3
    asyncio.run(consume_jetstream_message(retry, fail, dead_letter, max_deliver=3))
    assert retry.acked is True
    assert dead_letters == [("openkate.dlq.project.created.v1", retry.data)]
