from openkate_common.temporal import PlatformHeartbeatWorkflow


def test_temporal_baseline_workflow_and_task_queue_are_registered() -> None:
    assert PlatformHeartbeatWorkflow.__temporal_workflow_definition.name == "PlatformHeartbeatWorkflow"
    source = (open("services/workflow-service/app/temporal_worker.py")).read()
    assert 'task_queue="platform-baseline"' in source
    assert "PlatformHeartbeatWorkflow" in source
