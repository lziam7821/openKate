from typing import Any, Dict, List, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


SDK_VERSION = "1.0"
CONTRACT_VERSION = "1"


class ExecutorRequest(ApiModel):
    run_id: str = Field(alias="runId", min_length=1)
    step_id: str = Field(alias="stepId", min_length=1)
    action: str = Field(min_length=1)
    input: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    allowed_hosts: List[str] = Field(default_factory=list, alias="allowedHosts")
    timeout_ms: int = Field(default=10000, alias="timeoutMs", ge=100, le=300000)


class ExecutorResult(ApiModel):
    status: Literal["completed", "failed"]
    output: Dict[str, Any] = Field(default_factory=dict)
    input_summary: Dict[str, Any] = Field(default_factory=dict, alias="inputSummary")
    output_summary: Dict[str, Any] = Field(default_factory=dict, alias="outputSummary")
    assertions: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list, alias="evidenceRefs")
    environment: Dict[str, Any] = Field(default_factory=dict)


class ExecutorHealth(ApiModel):
    worker: str = Field(min_length=1)
    status: Literal["ready", "unavailable"]
    capabilities: List[str] = Field(min_length=1)
    sdk_version: str = Field(alias="sdkVersion")
    contract_version: str = Field(alias="contractVersion")


@runtime_checkable
class TestExecutor(Protocol):
    capabilities: List[str]

    def validate(self, request: ExecutorRequest) -> None:
        ...

    async def execute(self, request: ExecutorRequest) -> ExecutorResult:
        ...

    async def cancel(self, request: ExecutorRequest) -> None:
        ...
