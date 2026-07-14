import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from openkate_common.temporal import PlatformHeartbeatWorkflow


async def main() -> None:
    client = await Client.connect(
        os.getenv("OPENKATE_TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=os.getenv("OPENKATE_TEMPORAL_NAMESPACE", "openkate"),
    )
    worker = Worker(client, task_queue="platform-baseline", workflows=[PlatformHeartbeatWorkflow])
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
