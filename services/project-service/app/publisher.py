import asyncio
import json
import os

import nats
from nats.js.errors import NotFoundError

from app.store import ProjectStore
from openkate_common.messaging import publish_pending


async def main() -> None:
    store = ProjectStore(os.environ["OPENKATE_PROJECT_DATABASE_URL"])
    client = await nats.connect(os.getenv("OPENKATE_NATS_URL", "nats://127.0.0.1:4222"))
    jetstream = client.jetstream()
    try:
        await jetstream.stream_info("OPENKATE_EVENTS")
    except NotFoundError:
        await jetstream.add_stream(name="OPENKATE_EVENTS", subjects=["openkate.>"])
    try:
        while True:
            async def publish(subject: str, event: dict) -> None:
                await jetstream.publish(subject, json.dumps(event).encode())

            count = await publish_pending(store.pending_events(), publish, store.mark_published)
            await asyncio.sleep(0.1 if count else 1.0)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
