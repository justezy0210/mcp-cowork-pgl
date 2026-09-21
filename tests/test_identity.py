import sqlite3

import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import (
    EnvironmentCreate,
    EnvironmentVerification,
    Error,
    UserCreate,
    UserIdentity,
)
from cowork_hub.service import Hub
from cowork_hub.store import Store


def pending_environment(hub, user="alice"):
    return hub.register_environment(
        user,
        EnvironmentCreate(
            name="new",
            node_id="A",
            ssh_target=f"{user}@test",
            workdir="/work",
        ),
    )


def proof(pending, **changes):
    return EnvironmentVerification(
        challenge=pending["challenge"],
        container_id="new-container",
        cpus=8,
        memory_mib=16384,
        **{"uid": 1000, "gid": 1000, **changes},
    )


def test_unique_uid_shared_gid_and_atomic_user_creation(rig):
    with pytest.raises(Error) as caught:
        rig.hub.create_user(UserCreate(id="duplicate", identity={"uid": 1000, "gid": 2000}))
    assert caught.value.code == "UID_ALREADY_ASSIGNED"
    with rig.hub.store.transaction(write=False) as db:
        assert not db.execute("SELECT 1 FROM principals WHERE id='duplicate'").fetchone()
    assert rig.hub.user_identity("alice") == {"configured": True, "uid": 1000, "gid": 1000}
    assert rig.hub.user_identity("bob") == {"configured": True, "uid": 1001, "gid": 1000}


@pytest.mark.parametrize("changes", [{"uid": 5000}, {"gid": 5000}])
def test_mismatch_does_not_activate_environment_or_consume_proof(rig, changes):
    pending = pending_environment(rig.hub)
    with pytest.raises(Error) as caught:
        rig.hub.verify_environment("A", pending["id"], proof(pending, **changes))
    assert caught.value.code == "IDENTITY_MISMATCH"
    with rig.hub.store.transaction(write=False) as db:
        row = db.execute("SELECT * FROM environments WHERE id=?", (pending["id"],)).fetchone()
        assert row["status"] == "PENDING_VERIFICATION"
        assert row["container_id"] is None
        assert row["challenge_hash"] is not None
    assert rig.hub.verify_environment("A", pending["id"], proof(pending))["status"] == "READY"


def test_missing_identity_and_admin_setup(rig):
    token = rig.hub.create_user(UserCreate(id="new-user", allowed_nodes=["A"]))["token"]
    pending = pending_environment(rig.hub, "new-user")
    with pytest.raises(Error) as caught:
        rig.hub.verify_environment("A", pending["id"], proof(pending, uid=1002))
    assert caught.value.code == "USER_IDENTITY_REQUIRED"
    with TestClient(create_app(rig.hub, background=False)) as client:

        def auth(value):
            return {"Authorization": f"Bearer {value}"}

        assert client.get("/v1/identity", headers=auth(token)).json() == {"configured": False}
        url = "/v1/admin/users/new-user/identity"
        body = {"uid": 1002, "gid": 1000}
        for credential in (token, rig.worker):
            assert client.put(url, headers=auth(credential), json=body).status_code == 403
        assert client.put(url, headers=auth(rig.admin), json=body).status_code == 200
        assert client.get("/v1/identity", headers=auth(token)).json() == {
            "configured": True,
            **body,
        }
    assert (
        rig.hub.verify_environment("A", pending["id"], proof(pending, uid=1002))["status"]
        == "READY"
    )


def test_identity_update_preserves_verified_environments_and_active_jobs(rig):
    job = rig.submit()
    before = rig.hub.get_job("alice", job["id"])
    with pytest.raises(Error) as caught:
        rig.hub.set_user_identity("alice", UserIdentity(uid=1003, gid=1000))
    assert caught.value.code == "IDENTITY_IN_USE"
    rig.hub.set_user_identity("alice", UserIdentity(uid=1000, gid=1000))
    assert rig.hub.get_job("alice", job["id"]) == before
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 8


def test_v1_migration_preserves_credentials_jobs_and_blocks_unconfigured_dispatch(rig):
    first = rig.submit()
    queued = rig.submit()
    path = rig.hub.store.path
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE user_identities")
        db.execute("PRAGMA user_version=1")
    migrated = Hub(Store(path), clock=lambda: rig.now, heartbeat_timeout=30)
    assert migrated.authenticate(rig.alice)["id"] == "alice"
    assert migrated.get_job("alice", first["id"])["state"] == "DISPATCHING"
    assert migrated.get_job("alice", queued["id"])["state"] == "QUEUED"
    assert migrated.user_identity("alice") == {"configured": False}
    with migrated.store.transaction(write=False) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    rig.hub = migrated
    rig.event(first)
    assert rig.state(queued) == "QUEUED"
    rig.heartbeat()
    assert (
        next(env for env in migrated.list_environments("alice") if env["id"] == rig.env)["status"]
        == "UNAVAILABLE"
    )
    migrated.set_user_identity("alice", UserIdentity(uid=1000, gid=1000))
    rig.heartbeat()
    assert rig.state(queued) == "DISPATCHING"
