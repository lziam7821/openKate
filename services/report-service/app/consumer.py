import asyncio
import os

import httpx

REPORT_SERVICE_URL = os.getenv("OPENKATE_REPORT_SERVICE_URL", "http://127.0.0.1:8003")
VALIDATION_SERVICE_URL = os.getenv("OPENKATE_VALIDATION_SERVICE_URL", "http://127.0.0.1:8002")


async def consume_once(client: httpx.AsyncClient) -> int:
    events = (await client.get(f"{VALIDATION_SERVICE_URL}/internal/v1/events")).json()
    accepted = 0
    for event in events:
        response = await client.post(f"{REPORT_SERVICE_URL}/internal/v1/events", json=event)
        response.raise_for_status()
        accepted += int(response.json()["accepted"])
    return accepted


async def main() -> None:
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            try:
                await consume_once(client)
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
