import json
from typing import Any, Awaitable, Callable, Dict, Iterable, Set


async def publish_pending(
    events: Iterable[Dict[str, Any]],
    publish: Callable[[str, Dict[str, Any]], Awaitable[None]],
    mark_published: Callable[[str], None],
) -> int:
    published = 0
    for event in events:
        await publish(f"openkate.{event['eventType']}", event)
        mark_published(event["eventId"])
        published += 1
    return published


class IdempotentConsumer:
    def __init__(self) -> None:
        self.consumed_event_ids: Set[str] = set()

    def consume(self, event: Dict[str, Any], handler: Callable[[Dict[str, Any]], None]) -> bool:
        event_id = event["eventId"]
        if event_id in self.consumed_event_ids:
            return False
        handler(event)
        self.consumed_event_ids.add(event_id)
        return True


async def consume_jetstream_message(
    message: Any,
    handler: Callable[[Dict[str, Any]], Awaitable[None]],
    publish_dead_letter: Callable[[str, bytes], Awaitable[None]],
    max_deliver: int = 5,
) -> None:
    try:
        await handler(json.loads(message.data))
        await message.ack()
    except Exception:
        deliveries = message.metadata.num_delivered
        if deliveries >= max_deliver:
            await publish_dead_letter(f"openkate.dlq.{message.subject.removeprefix('openkate.')}", message.data)
            await message.ack()
        else:
            await message.nak(delay=min(2 ** deliveries, 30))
