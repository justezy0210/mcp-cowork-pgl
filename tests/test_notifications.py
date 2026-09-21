import json

import httpx
import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import Error
from cowork_hub.notifications import Destinations, Notifier


@pytest.fixture
def transport(tmp_path):
    calls = []
    replies = []

    def handle(request):
        calls.append(request)
        if request.method == "GET":
            channel = "456" if "222" in request.url.path else "123"
            return httpx.Response(200, json={"guild_id": "999", "channel_id": channel})
        return replies.pop(0) if replies else httpx.Response(200, json={"id": "message-id"})

    secrets_path = tmp_path / "fake-secrets.json"
    secrets_path.write_text(
        json.dumps(
            {
                "alice-ref": {
                    "user_id": "alice",
                    "channel_id": "123",
                    "url": "https://discord.com/api/webhooks/111/FAKE_TEST_ONLY",
                },
                "bob-ref": {
                    "user_id": "bob",
                    "channel_id": "456",
                    "url": "https://discord.com/api/webhooks/222/FAKE_TEST_ONLY",
                },
            }
        )
    )
    secrets_path.chmod(0o600)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    destinations = Destinations(secrets_path, "999", client)
    yield destinations, calls, replies
    destinations.close()


def records(rig):
    with rig.hub.store.transaction(write=False) as db:
        return [dict(r) for r in db.execute("SELECT * FROM notifications ORDER BY id")]


def test_destination_checks_owner_guild_and_channel(rig, transport):
    destinations, calls, _ = transport
    destinations.verify("alice", "alice-ref", "123")
    with pytest.raises(Error):
        destinations.verify("alice", "bob-ref", "456")
    assert len(calls) == 1  # Another user's secret was not even sent to Discord.
    destinations.guild_id = "wrong-guild"
    with pytest.raises(Error) as caught:
        destinations.verify("alice", "alice-ref", "123")
    assert caught.value.code == "DESTINATION_MISMATCH"


def test_notification_configuration_api_never_returns_webhook(rig, transport):
    destinations, _, _ = transport
    with TestClient(create_app(rig.hub, destinations, background=False)) as client:
        headers = {"Authorization": f"Bearer {rig.alice}"}
        response = client.put(
            "/v1/notifications",
            headers=headers,
            json={"secret_ref": "alice-ref", "channel_id": "123"},
        )
        assert response.status_code == 200
        assert "FAKE_TEST_ONLY" not in response.text
        assert "secret_ref" not in client.get("/v1/notifications", headers=headers).text
        response = client.put(
            "/v1/notifications",
            headers=headers,
            json={"secret_ref": "bob-ref", "channel_id": "456"},
        )
        assert response.status_code == 409


def test_notification_failure_does_not_block_next_assignment(rig, transport):
    destinations, calls, replies = transport
    notifier = Notifier(rig.hub, destinations)
    first = rig.submit(argv=["program", "--password", "secret-should-not-be-sent"])
    second = rig.submit()
    rig.event(first, "started")
    rig.event(first)
    assert rig.state(second) == "DISPATCHING"
    replies.append(httpx.Response(503))
    notifier.step()
    assert records(rig)[0]["state"] == "PENDING"
    notifier.step()  # End result can be sent while start is in backoff.
    assert [r["state"] for r in records(rig)] == ["SUPPRESSED", "SENT"]
    payload = json.loads(calls[-1].content)
    assert payload["allowed_mentions"] == {"parse": []}
    assert "secret-should-not-be-sent" not in calls[-1].content.decode()
    assert "wait=true" in str(calls[-1].url)


def test_rate_limit_and_restart_retry(rig, transport):
    destinations, calls, replies = transport
    job = rig.submit()
    rig.event(job)
    replies.append(httpx.Response(429, json={"retry_after": 10.0}))
    notifier = Notifier(rig.hub, destinations)
    notifier.step()
    row = records(rig)[0]
    assert row["state"] == "PENDING" and row["next_attempt"] == rig.now + 10
    assert not notifier.step()
    rig.now += 10
    Notifier(rig.hub, destinations).step()
    assert records(rig)[0]["state"] == "SENT"
    assert records(rig)[0]["attempts"] == 2
    assert len(calls) == 2


def test_permanent_error_visible_without_reexecution(rig, transport):
    destinations, _, replies = transport
    job = rig.submit()
    rig.event(job)
    replies.append(httpx.Response(404))
    Notifier(rig.hub, destinations).step()
    status = rig.hub.get_job("alice", job["id"])
    assert status["state"] == "SUCCEEDED"
    assert status["notifications"][0]["state"] == "FAILED"
    assert status["notifications"][0]["error_code"] == "DISCORD_HTTP_404"


def test_route_is_fixed_at_submission_and_late_start_is_suppressed(rig, transport):
    destinations, calls, _ = transport
    job = rig.submit()
    # A subsequent destination version must not modify this job's original route.
    rig.hub.provision_destination("alice", "new-ref", "789")
    rig.event(job)
    notifier = Notifier(rig.hub, destinations)
    notifier.step()
    assert "/111/" in calls[0].url.path
    rig.event(job, "started", occurred_at=rig.now - 1)
    assert records(rig)[1]["state"] == "SUPPRESSED"
    assert not notifier.step()
    assert rig.state(job) == "SUCCEEDED"


def test_abandoned_delivery_lease_is_recovered(rig, transport):
    destinations, _, _ = transport
    job = rig.submit()
    rig.event(job)
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE notifications SET state='SENDING',lease_until=?", (rig.now - 1,))
    assert Notifier(rig.hub, destinations).step()
    assert records(rig)[0]["state"] == "SENT"


def test_http_error_does_not_expose_webhook(rig, transport):
    destinations, _, _ = transport

    def fail(request):
        raise httpx.ConnectError(f"Connection failed: {request.url}", request=request)

    destinations.client.close()
    destinations.client = httpx.Client(transport=httpx.MockTransport(fail))
    with TestClient(create_app(rig.hub, destinations, background=False)) as client:
        response = client.put(
            "/v1/notifications",
            headers={"Authorization": f"Bearer {rig.alice}"},
            json={"secret_ref": "alice-ref", "channel_id": "123"},
        )
        assert response.status_code == 503
        assert "FAKE_TEST_ONLY" not in response.text


def test_existing_environment_webhook_is_scoped_and_verified_without_a_new_secret_file():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"guild_id": "999", "channel_id": "123"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    destinations = Destinations(None, "999", client)
    try:
        destinations.add_environment_webhook(
            user_id="alice",
            secret_ref="existing",
            channel_id="123",
            url="https://discord.com/api/webhooks/111/FAKE_TEST_ONLY",
        )
        destinations.verify("alice", "existing", "123")
        with pytest.raises(Error):
            destinations.verify("bob", "existing", "123")
        with pytest.raises(ValueError):
            destinations.add_environment_webhook(
                user_id="alice", secret_ref="incomplete", channel_id="123", url=None
            )
        assert calls == ["GET"]
    finally:
        destinations.close()
