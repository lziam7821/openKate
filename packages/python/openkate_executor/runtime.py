import asyncio
import inspect
from typing import Awaitable, Callable, Dict, List, Union

from .models import ExecutorRequest, ExecutorResult


Execution = Union[ExecutorResult, Awaitable[ExecutorResult]]


class ExecutorRuntime:
    """Small in-process implementation of the common executor lifecycle."""

    def __init__(self, capabilities: List[str], execute: Callable[[ExecutorRequest], Execution]) -> None:
        self.capabilities = capabilities
        self._execute = execute
        self._active: Dict[tuple[str, str], asyncio.Task] = {}

    def validate(self, request: ExecutorRequest) -> None:
        if not isinstance(request, ExecutorRequest):
            raise TypeError("request must be an ExecutorRequest")

    async def execute(self, request: ExecutorRequest) -> ExecutorResult:
        self.validate(request)
        key = (request.run_id, request.step_id)
        task = asyncio.current_task()
        if task is not None:
            self._active[key] = task
        try:
            result = self._execute(request)
            return await result if inspect.isawaitable(result) else result
        finally:
            if self._active.get(key) is task:
                self._active.pop(key, None)

    async def cancel(self, request: ExecutorRequest) -> None:
        task = self._active.get((request.run_id, request.step_id))
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
