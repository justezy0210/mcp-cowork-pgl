import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import EnvironmentCreate, EnvironmentVerification, Error, UserCreate


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_roles_ownership_and_error_redaction(rig):
    with TestClient(create_app(rig.hub, background=False)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/v1/jobs", headers=auth(rig.worker)).status_code == 403
        job = rig.submit()
        assert client.get(f"/v1/jobs/{job['id']}", headers=auth(rig.bob)).status_code == 404
        assert (
            client.post(
                "/v1/admin/nodes",
                headers=auth(rig.alice),
                json={"id": "B", "cpus": 4, "memory_mib": 1000},
            ).status_code
            == 403
        )
        result = client.post(
            "/v1/jobs", headers=auth(rig.alice), json={"secret": "never-echo-this"}
        )
        assert result.status_code == 422
        assert "never-echo-this" not in result.text
        result = client.get("/v1/jobs?limit=10000", headers=auth(rig.alice))
        assert result.status_code == 422
        result = client.post("/v1/jobs", headers=auth(rig.alice), content=b"x" * 300000)
        assert result.status_code == 413


def test_worker_cannot_report_another_nodes_execution(rig):
    with TestClient(create_app(rig.hub, background=False)) as client:
        node = client.post(
            "/v1/admin/nodes",
            headers=auth(rig.admin),
            json={
                "id": "B",
                "cpus": 8,
                "memory_mib": 16384,
            },
        ).json()
        job = rig.submit()
        response = client.post(
            f"/v1/worker/jobs/{job['id']}/events",
            headers=auth(node["worker_token"]),
            json={
                "event_id": "forged",
                "execution_id": job["execution_id"],
                "kind": "succeeded",
                "occurred_at": rig.now,
                "exit_code": 0,
            },
        )
        assert response.status_code == 404
        assert rig.state(job) == "DISPATCHING"


def test_environment_proof_scope_expiry_and_container_alias(rig):
    request = EnvironmentCreate(name="new", node_id="A", ssh_target="alice@new", workdir="/work")
    pending = rig.hub.register_environment("alice", request)
    with pytest.raises(Error) as caught:
        rig.submit(environment_ids=[pending["id"]])
    assert caught.value.code == "ENVIRONMENT_NOT_VERIFIED"
    verification = EnvironmentVerification(
        challenge=pending["challenge"],
        container_id="alice-container",
        uid=1000,
        gid=1000,
        cpus=8,
        memory_mib=16384,
        gpu_ids=["GPU-1"],
    )
    with pytest.raises(Error) as caught:
        rig.hub.verify_environment("B", pending["id"], verification)
    assert caught.value.code == "NOT_FOUND"
    with pytest.raises(Error) as caught:
        rig.hub.verify_environment("A", pending["id"], verification)
    assert caught.value.code == "CONTAINER_REGISTERED"
    verification.container_id = "new-container"
    rig.now += 601
    with pytest.raises(Error) as caught:
        rig.hub.verify_environment("A", pending["id"], verification)
    assert caught.value.code == "INVALID_PROOF"


def test_user_cannot_choose_other_environment_or_forbidden_node(rig):
    with pytest.raises(Error) as caught:
        rig.submit(environment_ids=[rig.bob_env])
    assert caught.value.code == "NOT_FOUND"
    rig.hub.set_grants("alice", [])
    with pytest.raises(Error) as caught:
        rig.hub.register_environment(
            "alice",
            EnvironmentCreate(
                name="test",
                node_id="A",
                ssh_target="root@test",
                workdir="/",
            ),
        )
    assert caught.value.code == "FORBIDDEN_NODE"


def test_destination_required_before_queueing(rig):
    rig.hub.create_user(
        UserCreate(
            id="charlie",
            allowed_nodes=["A"],
            identity={"uid": 1002, "gid": 1000},
        )
    )
    environment = rig.add_environment("charlie", "charlie-container")
    with pytest.raises(Error) as caught:
        rig.submit(user="charlie", environment_ids=[environment])
    assert caught.value.code == "NOTIFICATION_NOT_CONFIGURED"
    assert rig.hub.list_jobs("charlie") == []


def test_api_assignment_cursor_and_event_flow(rig):
    with TestClient(create_app(rig.hub, background=False)) as client:
        before = client.get("/v1/worker/assignments", headers=auth(rig.worker)).json()
        assert before["assignments"] == []
        job = client.post(
            "/v1/jobs",
            headers=auth(rig.alice),
            json={
                "request_key": "api-submit-1",
                "spec": rig.spec().model_dump(),
            },
        ).json()
        after = client.get(
            "/v1/worker/assignments",
            headers=auth(rig.worker),
            params={
                "since": before["revision"],
                "wait_seconds": 1,
            },
        ).json()
        assert after["revision"] != before["revision"]
        assert after["assignments"][0]["execution_id"] == job["execution_id"]
        assert "ssh_target" not in after["assignments"][0]["environment"]
        response = client.post(
            f"/v1/worker/jobs/{job['id']}/events",
            headers=auth(rig.worker),
            json={
                "event_id": "api-finished",
                "execution_id": job["execution_id"],
                "kind": "succeeded",
                "occurred_at": rig.now,
                "exit_code": 0,
            },
        )
        assert response.status_code == 200
        assert rig.state(job) == "SUCCEEDED"
