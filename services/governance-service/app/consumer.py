import asyncio
import os
from typing import Any, Dict, Optional, Set

import nats
import psycopg
from nats.js.api import AckPolicy, ConsumerConfig
from nats.js.errors import NotFoundError
from psycopg.types.json import Jsonb

from openkate_common.messaging import consume_jetstream_message


class ProjectEventConsumer:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url
        self.receipts: Set[str] = set()

    async def handle(self, event: Dict[str, Any]) -> None:
        if self.database_url is None:
            self.receipts.add(event["eventId"])
            return
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "INSERT INTO governance_schema.project_event_receipts (event_id, event_type, project_id, payload) VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (event["eventId"], event["eventType"], event["projectId"], Jsonb(event["payload"])),
            )


async def main() -> None:
    consumer = ProjectEventConsumer(os.environ["OPENKATE_GOVERNANCE_DATABASE_URL"])
    client = await nats.connect(os.getenv("OPENKATE_NATS_URL", "nats://127.0.0.1:4222"))
    jetstream = client.jetstream()
    try:
        await jetstream.stream_info("OPENKATE_EVENTS")
    except NotFoundError:
        await jetstream.add_stream(name="OPENKATE_EVENTS", subjects=["openkate.>"])

    async def publish_dead_letter(subject: str, data: bytes) -> None:
        await jetstream.publish(subject, data)

    async def callback(message: Any) -> None:
        await consume_jetstream_message(message, consumer.handle, publish_dead_letter)

    config = ConsumerConfig(durable_name="governance-project-events", ack_policy=AckPolicy.EXPLICIT, max_deliver=5)
    await jetstream.subscribe("openkate.project.>", cb=callback, manual_ack=True, config=config)
    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
