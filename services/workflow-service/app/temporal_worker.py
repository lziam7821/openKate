import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from app.main import DurableScenarioExecutionWorkflow, SCENARIO_TASK_QUEUE, execute_scenario_run
from openkate_common.temporal import PlatformHeartbeatWorkflow


async def main() -> None:
    client = await Client.connect(
        os.getenv("OPENKATE_TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=os.getenv("OPENKATE_TEMPORAL_NAMESPACE", "openkate"),
    )
    baseline_worker = Worker(client, task_queue="platform-baseline", workflows=[PlatformHeartbeatWorkflow])
    execution_worker = Worker(client, task_queue=SCENARIO_TASK_QUEUE, workflows=[DurableScenarioExecutionWorkflow], activities=[execute_scenario_run])
    await asyncio.gather(baseline_worker.run(), execution_worker.run())


if __name__ == "__main__":
    asyncio.run(main())
