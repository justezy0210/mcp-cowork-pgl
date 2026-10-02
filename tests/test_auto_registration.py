"""Account approval and registration checks replace per-container approval."""

import json

import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import Error, LocalEnvironment


def request(**changes):
    return LocalEnvironment(
        **{
            "name": "main",
            "node_id": "A",
            "ssh_target": "local",
            "workdir": "/work",
            "instance_id": "auto-main",
            "uid": 1000,
            "gid": 1000,
            "cpus": 4,
            "memory_mib": 256,
            "gpu_ids": ["GPU-1"],
            **changes,
        }
    )


def pending(rig, **changes):
    env = rig.hub.local.register("alice", request(**changes))
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE environments SET status='PENDING_APPROVAL' WHERE id=?", (env["id"],))
        db.execute("UPDATE local_environments SET approved=0 WHERE environment_id=?", (env["id"],))
    return env["id"]


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"node_id": "unassigned"}, "FORBIDDEN_NODE"),
        ({"uid": 1001}, "IDENTITY_MISMATCH"),
        ({"gid": 1001}, "IDENTITY_MISMATCH"),
        ({"cpus": 9}, "INVALID_CAPACITY"),
        ({"memory_mib": 16385}, "INVALID_CAPACITY"),
        ({"gpu_ids": ["GPU-other"]}, "INVALID_CAPACITY"),
    ],
)
def test_automatic_registration_preserves_checks(rig, changes, code):
    with pytest.raises(Error) as caught:
        rig.hub.local.register("alice", request(**changes))
    assert caught.value.code == code
    with rig.hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM local_environments").fetchone()[0] == 0


def test_disabled_or_unapproved_account_cannot_register(rig):
    with pytest.raises(Error) as caught:
        rig.hub.local.register("not-approved", request())
    assert caught.value.code == "FORBIDDEN"
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
    with pytest.raises(Error) as caught:
        rig.hub.local.register("alice", request())
    assert caught.value.code == "FORBIDDEN"


def test_startup_upgrades_pending_once_and_preserves_environment_ids(rig):
    env_id = pending(rig)
    with TestClient(create_app(rig.hub, background=False)):
        assert (
            next(e for e in rig.hub.list_environments("alice") if e["id"] == env_id)["status"]
            == "READY"
        )
    assert rig.hub.local.register("alice", request()) == {"id": env_id, "approved": True}
    assert rig.hub.local.activate_pending() == {"activated": [], "skipped": []}
    with rig.hub.store.transaction(write=False) as db:
        audit = db.execute(
            "SELECT * FROM web_audit WHERE action='environment.auto_register'"
        ).fetchall()
        assert len(audit) == 1
        assert audit[0]["target"] == env_id and audit[0]["actor"] == "alice"
        assert json.loads(audit[0]["before_value"])["status"] == "PENDING_APPROVAL"


def test_reregistration_rechecks_pending_and_keeps_unavailable_state(rig):
    env_id = pending(rig)
    assert rig.hub.local.register("alice", request()) == {"id": env_id, "approved": True}
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE environments SET status='UNAVAILABLE' WHERE id=?", (env_id,))
    rig.hub.local.register("alice", request())
    rig.hub.local.activate_pending()
    assert (
        next(e for e in rig.hub.list_environments("alice") if e["id"] == env_id)["status"]
        == "UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DELETE FROM grants WHERE user_id='alice'", "FORBIDDEN_NODE"),
        ("UPDATE principals SET enabled=0 WHERE id='alice'", "FORBIDDEN"),
        ("UPDATE nodes SET enabled=0 WHERE id='A'", "FORBIDDEN_NODE"),
        ("UPDATE user_identities SET uid=1002 WHERE user_id='alice'", "IDENTITY_MISMATCH"),
        ("UPDATE user_identities SET gid=1002 WHERE user_id='alice'", "IDENTITY_MISMATCH"),
        ("DELETE FROM user_identities WHERE user_id='alice'", "USER_IDENTITY_REQUIRED"),
        ("UPDATE nodes SET cpus=1 WHERE id='A'", "INVALID_CAPACITY"),
        ("UPDATE nodes SET memory_mib=128 WHERE id='A'", "INVALID_CAPACITY"),
        ("UPDATE nodes SET gpus='[]' WHERE id='A'", "INVALID_CAPACITY"),
    ],
)
def test_pending_upgrade_rechecks_current_permissions_and_capacity(rig, sql, code):
    env_id = pending(rig)
    with rig.hub.store.transaction() as db:
        db.execute(sql)
    assert rig.hub.local.activate_pending() == {
        "activated": [],
        "skipped": [{"id": env_id, "error": code}],
    }
    with rig.hub.store.transaction(write=False) as db:
        assert (
            db.execute(
                "SELECT approved FROM local_environments WHERE environment_id=?", (env_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT status FROM environments WHERE id=?", (env_id,)).fetchone()[0]
            == "PENDING_APPROVAL"
        )
        assert db.execute("SELECT COUNT(*) FROM web_audit").fetchone()[0] == 0
