import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from test_management import connector
from test_web import auth, settings, verify

from cowork_hub.api import create_app
from cowork_hub.models import Error
from cowork_hub.notifications import Destinations
from cowork_hub.onboarding import Enrollment, Onboarding
from cowork_hub.ssh_input import parse_ssh_command
from cowork_hub.store import SCHEMA_VERSION

SECRET = "https://discord.com/api/webhooks/333/FAKE_ENROLLMENT_ONLY"
DETAILS = {"account_name": "carol", "uid": 1200, "gid": 1000, "webhook_url": SECRET}


@pytest.fixture
def enrollment_web(rig, tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"guild_id": "999", "channel_id": "789"})

    destinations = Destinations(
        None,
        "999",
        httpx.Client(transport=httpx.MockTransport(handle)),
        managed_path=tmp_path / "discord-users.json",
    )
    config = settings(admin_users=["alice"])
    with TestClient(
        create_app(rig.hub, destinations, background=False, web_config=config, web_verify=verify)
    ) as client:
        yield client, destinations, calls, config


def test_enrollment_is_required_private_and_blocked_until_admin_approval(rig, enrollment_web):
    client, destinations, calls, _ = enrollment_web
    carol = auth("google-carol")
    assert client.get("/v1/web/onboarding", headers=carol).json() == {"state": "NEW"}
    for field in DETAILS:
        response = client.post(
            "/v1/web/onboarding",
            headers=carol,
            json={k: v for k, v in DETAILS.items() if k != field},
        )
        assert response.status_code == 422
        assert "FAKE_ENROLLMENT_ONLY" not in response.text
    assert not calls
    assert client.post("/v1/web/onboarding", json=DETAILS).status_code == 401
    assert (
        client.post("/v1/web/onboarding", headers=auth(rig.admin), json=DETAILS).status_code == 401
    )
    created = client.post("/v1/web/onboarding", headers=carol, json=DETAILS)
    assert created.status_code == 201
    request = created.json()
    assert request["state"] == "PENDING" and request["account_name"] == "carol"
    assert created.headers["cache-control"] == "no-store"
    assert all(value not in created.text for value in [SECRET, "secret_ref", "firebase_uid"])
    assert client.post("/v1/web/onboarding", headers=carol, json=DETAILS).json() == request
    assert len(calls) == 1  # A lost response can be retried without another Discord request.
    assert client.get("/v1/web/onboarding", headers=auth("google-dave")).json() == {"state": "NEW"}
    assert client.get("/v1/web/profile", headers=carol).status_code == 403
    assert client.post("/v1/web/tokens", headers=carol, json={"name": "main"}).status_code == 403
    with rig.hub.store.transaction(write=False) as db:
        assert not db.execute("SELECT 1 FROM principals WHERE id='carol'").fetchone()
        assert "FAKE_ENROLLMENT_ONLY" not in json.dumps(
            dict(
                db.execute("SELECT * FROM web_enrollments WHERE id=?", (request["id"],)).fetchone()
            )
        )
    route = "/v1/web/admin/enrollments/" + request["id"] + "/review"
    review = {"decision": "APPROVED", "allowed_nodes": ["A"]}
    assert client.get("/v1/web/admin/enrollments", headers=carol).status_code == 403
    assert client.post(route, headers=carol, json=review).status_code == 403
    assert client.post(route, headers=auth("google-bob"), json=review).status_code == 403
    assert (
        client.post(route, headers=auth(), json={**review, "allowed_nodes": []}).status_code == 422
    )
    assert client.post(route, headers=auth(), json=review).json()["state"] == "APPROVED"
    assert client.post(route, headers=auth(), json=review).json()["state"] == "APPROVED"
    profile = client.get("/v1/web/profile", headers=carol).json()
    assert profile["identity"] == {"configured": True, "uid": 1200, "gid": 1000}
    assert profile["allowed_nodes"] == ["A"] and not profile["is_admin"]
    assert profile["notification"]["channel_id"] == "789"
    assert client.get("/v1/web/onboarding", headers=carol).json() == {"state": "LINKED"}
    token = client.post("/v1/web/tokens", headers=carol, json={"name": "main"}).json()["token"]
    assert rig.hub.authenticate(token)["id"] == "carol"
    assert destinations.managed_path.stat().st_mode & 0o777 == 0o600
    audit = client.get("/v1/web/admin/audit", headers=auth()).json()
    assert audit["total"] == 1 and audit["items"][0]["action"] == "account.review"
    assert "FAKE_ENROLLMENT_ONLY" not in json.dumps(audit)


def test_enrollment_cannot_take_existing_names_uids_or_grant_itself_access(rig, enrollment_web):
    client, _, calls, _ = enrollment_web
    carol = auth("google-carol")
    for body in ({**DETAILS, "account_name": "alice"}, {**DETAILS, "uid": 1000}):
        assert client.post("/v1/web/onboarding", headers=carol, json=body).status_code == 409
    for extra in ({"allowed_nodes": ["A"]}, {"firebase_uid": "google-alice"}, {"is_admin": True}):
        assert (
            client.post("/v1/web/onboarding", headers=carol, json={**DETAILS, **extra}).status_code
            == 422
        )
    assert not calls
    assert client.post("/v1/web/onboarding", headers=auth(), json=DETAILS).status_code == 409
    request = client.post("/v1/web/onboarding", headers=carol, json=DETAILS).json()
    assert (
        client.post("/v1/web/onboarding", headers=auth("google-dave"), json=DETAILS).status_code
        == 409
    )
    route = "/v1/web/admin/enrollments/" + request["id"] + "/review"
    # Invalid grants roll back principal, identity and Google mapping together.
    assert (
        client.post(
            route, headers=auth(), json={"decision": "APPROVED", "allowed_nodes": ["missing"]}
        ).status_code
        == 404
    )
    assert client.get("/v1/web/profile", headers=carol).status_code == 403
    assert (
        client.post(route, headers=auth(), json={"decision": "REJECTED"}).json()["state"]
        == "REJECTED"
    )
    again = client.post("/v1/web/onboarding", headers=carol, json=DETAILS).json()
    assert again["state"] == "PENDING" and again["id"] != request["id"]
    assert (
        client.post(
            route, headers=auth(), json={"decision": "APPROVED", "allowed_nodes": ["A"]}
        ).status_code
        == 404
    )


def test_failed_webhook_never_creates_an_account_and_requests_survive_restart(rig, enrollment_web):
    client, destinations, _, config = enrollment_web
    destinations.guild_id = "wrong-guild"
    assert (
        client.post("/v1/web/onboarding", headers=auth("google-carol"), json=DETAILS).status_code
        == 409
    )
    assert client.get("/v1/web/onboarding", headers=auth("google-carol")).json() == {"state": "NEW"}
    destinations.guild_id = "999"
    identities = [{"firebase_uid": "google-carol", "email": "carol@example.test"}] * 2
    service = Onboarding(rig.hub, config, destinations)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.submit, identities, [Enrollment(**DETAILS)] * 2))
    assert results[0]["id"] == results[1]["id"]
    reopened = Onboarding(type(rig.hub)(rig.reopen_store()), config, destinations)
    assert reopened.status(identities[0])["id"] == results[0]["id"]


def test_rejected_enrollment_can_correct_name_and_reuse_its_own_channel(rig, enrollment_web):
    client, destinations, _, _ = enrollment_web
    carol = auth("google-carol")
    request = client.post("/v1/web/onboarding", headers=carol, json=DETAILS).json()
    old_ref = next(iter(destinations.managed_entries))
    client.post(
        "/v1/web/admin/enrollments/" + request["id"] + "/review",
        headers=auth(),
        json={"decision": "REJECTED"},
    )
    corrected = client.post(
        "/v1/web/onboarding", headers=carol, json={**DETAILS, "account_name": "caroline"}
    )
    assert corrected.status_code == 201
    assert old_ref not in destinations.entries
    assert len(destinations.managed_entries) == 1
    restored = Destinations(None, "999", managed_path=destinations.managed_path)
    try:
        assert restored.managed_entries == destinations.managed_entries
    finally:
        restored.close()
    approved = client.post(
        "/v1/web/admin/enrollments/" + corrected.json()["id"] + "/review",
        headers=auth(),
        json={"decision": "APPROVED", "allowed_nodes": ["A"]},
    )
    assert approved.status_code == 200
    assert client.get("/v1/web/profile", headers=carol).json()["user_id"] == "caroline"
    # Cleanup cannot touch an approved destination, including its immutable job references.
    Onboarding(rig.hub, enrollment_web[3], destinations)._release_rejected("google-carol")
    assert len(destinations.managed_entries) == 1


@pytest.mark.parametrize(
    "command,expected",
    [
        ("ssh -p 11010 alice@host.example", ("alice", "host.example", 11010)),
        ("ssh alice@192.0.2.1 -p11010", ("alice", "192.0.2.1", 11010)),
        ("ssh alice@host.example", ("alice", "host.example", 22)),
        ("ssh -p 22 'alice@[2001:db8::1]'", ("alice", "2001:db8::1", 22)),
    ],
)
def test_ssh_command_is_parsed_without_execution(command, expected):
    target = parse_ssh_command(command)
    assert (target.user, target.host, target.port) == expected


@pytest.mark.parametrize(
    "command",
    [
        "ssh alice@host touch /tmp/unsafe",
        "ssh alice@host;id",
        "ssh alice@$(id)",
        "ssh -o ProxyCommand=anything alice@host",
        "ssh -i secret.key alice@host",
        "ssh -p 22 -p 23 alice@host",
        "ssh -p",
        "ssh -p 0 alice@host",
        "ssh alias",
        "ssh alice@host\n",
        "ssh 'alice@host",
        "scp alice@host",
    ],
)
def test_ssh_command_rejects_shells_extra_options_and_ambiguous_targets(command):
    with pytest.raises(Error) as caught:
        parse_ssh_command(command)
    assert caught.value.code == "INVALID_SSH_COMMAND"


def test_container_form_uses_main_shared_path_and_preserves_approval(rig, enrollment_web):
    client, _, _, _ = enrollment_web
    main = connector(rig)
    body = {
        "connector_id": main["id"],
        "node_id": "A",
        "request_key": "ssh-form-001",
        "ssh_command": "ssh -p 11010 alice@host.example",
    }
    assert (
        client.post("/v1/web/containers", headers=auth("google-bob"), json=body).status_code == 404
    )
    assert (
        client.post(
            "/v1/web/containers", headers=auth(), json={**body, "node_id": "missing"}
        ).status_code
        == 403
    )
    result = client.post("/v1/web/containers", headers=auth(), json=body)
    assert result.status_code == 201
    request = result.json()
    assert request["state"] == "PENDING"
    assert request["target"] == {
        "host": "host.example",
        "user": "alice",
        "port": 11010,
        "node_id": "A",
        "workdir": "/work",
    }
    assert (
        client.post("/v1/web/containers", headers=auth(), json=body).json()["id"] == request["id"]
    )
    assert (
        client.post(
            "/v1/web/containers", headers=auth(), json={**body, "workdir": "/other"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/web/ssh/parse", headers=auth(), json={"ssh_command": body["ssh_command"]}
        ).json()["port"]
        == 11010
    )


def test_schema_five_upgrade_preserves_existing_rows(rig):
    job = rig.submit(cpus=1)
    with rig.hub.store.transaction() as db:
        db.execute("DROP TABLE web_enrollments")
        db.execute(
            "PRAGMA user_version=5"
            if rig.hub.store.backend == "sqlite"
            else "UPDATE hub_schema SET version=5 WHERE id=1"
        )
    store = rig.reopen_store()
    with store.transaction(write=False) as db:
        version = db.execute(
            "PRAGMA user_version"
            if store.backend == "sqlite"
            else "SELECT version FROM hub_schema WHERE id=1"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION
        assert db.execute("SELECT COUNT(*) FROM web_enrollments").fetchone()[0] == 0
        assert (
            db.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == "DISPATCHING"
        )
    assert rig.hub.authenticate(rig.alice)["id"] == "alice"
