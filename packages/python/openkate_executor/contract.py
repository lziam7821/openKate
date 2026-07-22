from typing import Any, Dict

from .models import CONTRACT_VERSION, SDK_VERSION, ExecutorHealth, ExecutorResult


def assert_health_contract(payload: Dict[str, Any]) -> ExecutorHealth:
    health = ExecutorHealth.model_validate(payload)
    assert health.status == "ready"
    assert health.sdk_version == SDK_VERSION
    assert health.contract_version == CONTRACT_VERSION
    return health


def assert_result_contract(payload: Dict[str, Any]) -> ExecutorResult:
    result = ExecutorResult.model_validate(payload)
    assert result.status == "completed"
    return result
