from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.connector_models import (
    ConnectorCreate,
    ConnectorPoll,
    RegistrationCreate,
    RegistrationResult,
)
from cowork_hub.models import Error, LocalEnvironment
from cowork_hub.service import Hub
from cowork_hub.store import Store


def auth(token):
    return {"Authorization": "Bearer " + token}


def connector(rig):
    return rig.hub.management.register_connector(
        "alice", ConnectorCreate(instance_id="main-instance", name="main", environment_id=rig.env)
    )


def request(connector_id, key="registration-1", **target):
    return RegistrationCreate(
        request_key=key,
        connector_id=connector_id,
        target={
            "node_id": "A",
            "host": "container.example",
            "user": "alice",
            "port": 11010,
            "workdir": "/work",
            **target,
        },
    )


def registered_environment(rig, body):
    return rig.hub.local.register(
        "alice",
        LocalEnvironment(
            name="target",
            node_id=body.target.node_id,
            ssh_target=f"ssh -p {body.target.port} {body.target.user}@{body.target.host}",
            workdir=body.target.workdir,
            instance_id="new-target",
            uid=1000,
            gid=1000,
            cpus=1,
            memory_mib=64,
        ),
    )["id"]


def test_personal_tokens_are_private_revocable_and_preserve_jobs(rig):
    job = rig.submit(cpus=1)
    with TestClient(create_app(rig.hub, background=False)) as client:
        first = client.post("/v1/tokens", json={"name": "main"}, headers=auth(rig.alice))
        assert first.status_code == 201 and first.headers["cache-control"] == "no-store"
        first = first.json()
        second = client.post("/v1/tokens", json={"name": "other"}, headers=auth(rig.alice)).json()
        assert client.get("/v1/identity", headers=auth(first["token"])).json()["uid"] == 1000
        listing = client.get("/v1/tokens", headers=auth(rig.alice))
        assert len(listing.json()) == 2
        assert first["token"] not in listing.text and "token_hash" not in listing.text
        assert client.get("/v1/tokens", headers=auth(rig.bob)).json() == []
        assert client.delete("/v1/tokens/" + first["id"], headers=auth(rig.bob)).status_code == 404
        assert (
            client.post("/v1/tokens", json={"name": "admin"}, headers=auth(rig.admin)).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/admin/nodes",
                json={"id": "B", "cpus": 1, "memory_mib": 64},
                headers=auth(first["token"]),
            ).status_code
            == 403
        )
        assert client.delete("/v1/tokens/" + first["id"], headers=auth(rig.alice)).json()["revoked"]
        assert (
            client.delete("/v1/tokens/" + first["id"], headers=auth(rig.alice)).status_code == 200
        )
        assert client.get("/v1/identity", headers=auth(first["token"])).status_code == 401
        assert client.get("/v1/identity", headers=auth(second["token"])).status_code == 200
        assert client.get("/v1/identity", headers=auth(rig.alice)).status_code == 200
        assert rig.state(job) == "DISPATCHING"
        rig.hub.set_grants("alice", [])
        assert client.get("/v1/cluster", headers=auth(second["token"])).json() == []
        with rig.hub.store.transaction() as db:
            db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
        assert client.get("/v1/identity", headers=auth(second["token"])).status_code == 401


def test_registration_claims_restart_and_approval_are_separate(rig):
    service = rig.hub.management
    registered = connector(rig)
    body = request(registered["id"])
    queued = service.request_registration("alice", body)
    assert service.request_registration("alice", body)["id"] == queued["id"]
    with pytest.raises(Error, match="different data"):
        service.request_registration("alice", request(registered["id"], host="another.example"))
    assert not service.list_connectors("alice")[0]["online"]
    poll = ConnectorPoll(instance_id="main-instance")
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: service.poll("alice", registered["id"], poll), range(2)))
    first = next(a["request"] for a in answers if a["request"])
    assert sum(a["request"] is not None for a in answers) == 1
    assert service.list_connectors("alice")[0]["online"]
    rig.now += 121
    assert not service.list_connectors("alice")[0]["online"]
    restarted = Hub(Store(rig.hub.store.path), clock=lambda: rig.now).management
    second = restarted.poll("alice", registered["id"], poll)["request"]
    assert second["id"] == first["id"] and second["claim_id"] != first["claim_id"]
    env_id = registered_environment(rig, body)
    outcome = RegistrationResult(
        instance_id="main-instance", claim_id=first["claim_id"], environment_id=env_id
    )
    with pytest.raises(Error, match="assigned again"):
        restarted.complete("alice", registered["id"], queued["id"], outcome)
    outcome.claim_id = second["claim_id"]
    completed = restarted.complete("alice", registered["id"], queued["id"], outcome)
    assert completed["state"] == "REGISTERED"
    assert restarted.complete("alice", registered["id"], queued["id"], outcome) == completed
    pending = restarted.pending_environments()
    assert pending[0]["id"] == env_id and pending[0]["user_id"] == "alice"
    assert (
        next(e for e in rig.hub.list_environments("alice") if e["id"] == env_id)["status"]
        == "PENDING_APPROVAL"
    )
    with TestClient(create_app(rig.hub, background=False)) as client:
        url = "/v1/admin/local/environments/" + env_id + "/approve"
        assert (
            client.get("/v1/admin/local/environments/pending", headers=auth(rig.alice)).status_code
            == 403
        )
        assert client.post(url, headers=auth(rig.alice)).status_code == 403
        assert client.post(url, headers=auth(rig.admin)).json()["approved"]
    assert rig.hub.management.pending_environments() == []


def test_connector_ownership_grants_and_fixed_request_schema(rig):
    service = rig.hub.management
    registered = connector(rig)
    assert connector(rig) == registered
    with pytest.raises(Error):
        service.register_connector(
            "bob",
            ConnectorCreate(instance_id="main-instance", name="forged", environment_id=rig.bob_env),
        )
    with pytest.raises(Error):
        service.request_registration("bob", request(registered["id"]))
    with pytest.raises(Error):
        service.poll("alice", registered["id"], ConnectorPoll(instance_id="wrong-instance"))
    with pytest.raises(Error):
        service.request_registration("alice", request(registered["id"], node_id="forbidden"))
    queued = service.request_registration("alice", request(registered["id"]))
    rig.hub.set_grants("alice", [])
    assert (
        service.poll("alice", registered["id"], ConnectorPoll(instance_id="main-instance"))[
            "request"
        ]
        is None
    )
    assert service.list_requests("alice")[0]["error_code"] == "FORBIDDEN_NODE"
    assert service.list_requests("bob") == []
    with TestClient(create_app(rig.hub, background=False)) as client:
        payload = request(registered["id"]).model_dump()
        payload["argv"] = ["do-not-execute-this"]
        response = client.post("/v1/registration-requests", json=payload, headers=auth(rig.alice))
        assert response.status_code == 422 and "do-not-execute-this" not in response.text
        assert client.get("/v1/registration-requests", headers=auth(rig.bob)).json() == []
        assert (
            client.post(
                "/v1/connectors/" + registered["id"] + "/poll",
                json={"instance_id": "main-instance"},
                headers=auth(rig.bob),
            ).status_code
            == 404
        )
    assert queued["id"] == service.list_requests("alice")[0]["id"]


def test_registration_result_cannot_bind_another_environment(rig):
    service = rig.hub.management
    registered = connector(rig)
    queued = service.request_registration("alice", request(registered["id"]))
    claimed = service.poll("alice", registered["id"], ConnectorPoll(instance_id="main-instance"))[
        "request"
    ]
    with pytest.raises(Error, match="does not match"):
        service.complete(
            "alice",
            registered["id"],
            queued["id"],
            RegistrationResult(
                instance_id="main-instance",
                claim_id=claimed["claim_id"],
                environment_id=rig.bob_env,
            ),
        )
    assert service.list_requests("alice")[0]["state"] == "RUNNING"


def test_schema_three_upgrade_preserves_credentials_and_reservations(rig):
    job = rig.submit(cpus=2)
    with rig.hub.store.transaction() as db:
        db.execute("DROP TABLE registration_requests")
        db.execute("DROP TABLE connectors")
        db.execute("DROP TABLE user_tokens")
        db.execute("PRAGMA user_version=3")
    hub = Hub(Store(rig.hub.store.path))
    assert hub.authenticate(rig.alice)["id"] == "alice"
    assert hub.authenticate(rig.admin)["role"] == "admin"
    assert hub.get_job("alice", job["id"])["state"] == "DISPATCHING"
    assert hub.cluster("alice")[0]["reserved_cpus"] == 2
    assert hub.management.list_tokens("alice") == []
