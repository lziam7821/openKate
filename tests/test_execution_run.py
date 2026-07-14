from datetime import datetime, timedelta, timezone

from test_execution_plan import client, execution_service, reset_store, valid_plan


def create_plan() -> dict:
    response = client.post(
        "/internal/v1/scenarios/scenario_checkout/execution-plans",
        headers={"X-OpenKATE-Project-Id": "project_checkout"},
        json=valid_plan(),
    )
    assert response.status_code == 201
    return response.json()


def create_run(plan_id: str, key: str = "run-once"):
    return client.post(
        "/internal/v1/scenarios/scenario_checkout/runs",
        headers={"X-OpenKATE-Project-Id": "project_checkout", "Idempotency-Key": key},
        json={"planId": plan_id, "environmentId": "staging", "variables": {}},
    )


def test_run_command_is_idempotent_and_leases_are_isolated() -> None:
    reset_store()
    plan = create_plan()
    first = create_run(plan["id"])
    duplicate = create_run(plan["id"])
    assert first.status_code == 202
    assert duplicate.json()["id"] == first.json()["id"]

    run_ids = {first.json()["id"]}
    lease_ids = {first.json()["leaseId"]}
    for index in range(1, 10):
        run = create_run(plan["id"], f"run-{index}").json()
        run_ids.add(run["id"])
        lease_ids.add(run["leaseId"])
    assert len(run_ids) == 10
    assert len(lease_ids) == 10
    assert len({execution_service.store.leases[lease_id]["browserContextId"] for lease_id in lease_ids}) == 10


def test_steps_follow_dependencies_transfer_variables_and_release_lease() -> None:
    reset_store()
    plan = create_plan()
    run = create_run(plan["id"]).json()
    run_id = run["id"]
    assert client.post(f"/internal/v1/runs/{run_id}/steps/pay_order/start").status_code == 409

    assert client.post(f"/internal/v1/runs/{run_id}/steps/place_order/start").status_code == 200
    assert client.post(f"/internal/v1/runs/{run_id}/steps/place_order/complete", json={"output": {"order": {"id": "order-42"}}, "evidenceRefs": ["s3://shot.png"]}).status_code == 200
    assert client.post(f"/internal/v1/runs/{run_id}/steps/pay_order/start").status_code == 200
    assert client.post(f"/internal/v1/runs/{run_id}/steps/pay_order/complete", json={"output": {"traceId": "trace-7"}}).status_code == 200
    assert client.post(f"/internal/v1/runs/{run_id}/steps/verify_order/start").status_code == 200
    assert client.post(f"/internal/v1/runs/{run_id}/steps/verify_order/complete", json={"output": {"status": "PAID"}, "assertions": [{"passed": True}]}).status_code == 200

    completed = client.get(f"/internal/v1/runs/{run_id}").json()
    assert completed["status"] == "completed"
    assert "orderId" in completed["variables"]
    assert execution_service.store.runs[run_id]["_variables"]["orderId"] == "order-42"
    assert execution_service.store.leases[run["leaseId"]]["status"] == "released"
    event_types = [event["eventType"] for event in client.get(f"/internal/v1/runs/{run_id}/events").json()["events"]]
    assert event_types[-1] == "execution.run.completed.v1"


def test_cancel_releases_resources_and_failed_run_can_retry() -> None:
    reset_store()
    plan = create_plan()
    run = create_run(plan["id"]).json()
    canceled = client.post(f"/internal/v1/runs/{run['id']}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert execution_service.store.leases[run["leaseId"]]["status"] == "released"

    retried = client.post(f"/internal/v1/runs/{run['id']}/retry", headers={"Idempotency-Key": "retry-1"})
    assert retried.status_code == 202
    assert retried.json()["retryOf"] == run["id"]
    assert retried.json()["leaseId"] != run["leaseId"]
    assert retried.json()["attempt"] == 2


def test_configured_account_and_dataset_cannot_be_leased_twice() -> None:
    reset_store()
    plan = create_plan()
    payload = {
        "planId": plan["id"],
        "environmentId": "staging",
        "allowedHosts": ["shop.test"],
        "accountRefs": ["account-ref-1"],
        "dataSetRefs": ["dataset-ref-1"],
    }
    first = client.post(
        "/internal/v1/scenarios/scenario_checkout/runs",
        headers={"X-OpenKATE-Project-Id": "project_checkout", "Idempotency-Key": "pool-1"},
        json=payload,
    )
    assert first.status_code == 202
    blocked = client.post(
        "/internal/v1/scenarios/scenario_checkout/runs",
        headers={"X-OpenKATE-Project-Id": "project_checkout", "Idempotency-Key": "pool-2"},
        json=payload,
    )
    assert blocked.status_code == 409
    client.post(f"/internal/v1/runs/{first.json()['id']}/cancel")
    available = client.post(
        "/internal/v1/scenarios/scenario_checkout/runs",
        headers={"X-OpenKATE-Project-Id": "project_checkout", "Idempotency-Key": "pool-3"},
        json=payload,
    )
    assert available.status_code == 202


def test_deadline_failure_releases_lease() -> None:
    reset_store()
    plan = create_plan()
    run = create_run(plan["id"]).json()
    execution_service.store.runs[run["id"]]["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    response = client.post(f"/internal/v1/runs/{run['id']}/steps/place_order/start")
    assert response.status_code == 408
    assert execution_service.store.runs[run["id"]]["status"] == "failed"
    assert execution_service.store.leases[run["leaseId"]]["status"] == "released"


def test_sensitive_variables_are_classified_and_never_returned() -> None:
    reset_store()
    payload = valid_plan()
    payload["variables"]["access_token"] = "secret-value"
    plan = client.post(
        "/internal/v1/scenarios/scenario_checkout/execution-plans",
        headers={"X-OpenKATE-Project-Id": "project_checkout"},
        json=payload,
    ).json()
    run = create_run(plan["id"], "sensitive-run").json()
    stored = execution_service.store.runs[run["id"]]
    assert stored["_sensitiveVariables"] == ["access_token"]
    assert "secret-value" not in str(run)
    assert "secret-value" not in str(client.get(f"/internal/v1/runs/{run['id']}/events").json())
