from datetime import timedelta

from temporalio import workflow


@workflow.defn
class PlatformHeartbeatWorkflow:
    @workflow.run
    async def run(self, service: str) -> dict[str, str]:
        await workflow.sleep(timedelta(milliseconds=10))
        return {"service": service, "status": "ready"}
