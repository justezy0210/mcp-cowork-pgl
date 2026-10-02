import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from test_web import auth, settings, verify

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
            channel = {"111": "123", "222": "456", "333": "789", "444": "987"}[
                request.url.path.split("/")[-2]
            ]
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
    assert caught.value.code == "DESTINATION_GUILD_NOT_ALLOWED"


def test_additional_guild_preserves_old_routes_and_rejects_unlisted_servers(transport, tmp_path):
    def metadata(request):
        guild, channel = {"111": ("999", "123"), "333": ("888", "789"), "444": ("777", "987")}[
            request.url.path.split("/")[-2]
        ]
        return httpx.Response(200, json={"guild_id": guild, "channel_id": channel})

    client = httpx.Client(transport=httpx.MockTransport(metadata))
    managed = tmp_path / "managed.json"
    destination = Destinations(
        tmp_path / "fake-secrets.json",
        "999",
        client,
        managed_path=managed,
        additional_guild_ids=[" 888 ", ""],
    )
    new_url = "https://discord.com/api/webhooks/333/FAKE_NEW_SERVER"
    try:
        destination.verify("alice", "alice-ref", "123")
        ref, channel = destination.register("carol", new_url)
        destination.verify("carol", ref, channel)
        # Additional servers do not permit sharing another user's channel.
        with pytest.raises(Error) as conflict:
            destination.register("dave", new_url)
        assert conflict.value.code == "DESTINATION_IN_USE"
        with pytest.raises(Error) as unlisted:
            destination.register("dave", "https://discord.com/api/webhooks/444/FAKE_UNLISTED")
        assert unlisted.value.code == "DESTINATION_GUILD_NOT_ALLOWED"
        assert len(destination.managed_entries) == 1
        assert "FAKE_UNLISTED" not in managed.read_text()
        reopened = Destinations(
            tmp_path / "fake-secrets.json",
            "999",
            client,
            managed_path=managed,
            additional_guild_ids=["888"],
        )
        reopened.verify("alice", "alice-ref", "123")
        reopened.verify("carol", ref, channel)
        # Omitting the extra server still enforces the original restriction.
        restricted = Destinations(
            tmp_path / "fake-secrets.json", "999", client, managed_path=managed
        )
        restricted.verify("alice", "alice-ref", "123")
        with pytest.raises(Error) as blocked:
            restricted.verify("carol", ref, channel)
        assert blocked.value.code == "DESTINATION_GUILD_NOT_ALLOWED"
    finally:
        destination.close()


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


def test_admin_registers_persistent_private_channels_and_receive_only_their_jobs(
    rig, transport, tmp_path
):
    destinations, calls, _ = transport
    destinations.managed_path = tmp_path / "discord-users.json"
    alice_url = "https://discord.com/api/webhooks/333/FAKE_ALICE_WEB_SECRET"
    bob_url = "https://discord.com/api/webhooks/222/FAKE_BOB_WEB_SECRET"
    old_job = rig.submit(cpus=1, memory_mib=256)
    app = create_app(
        rig.hub,
        destinations,
        background=False,
        web_config=settings(admin_users=["alice"]),
        web_verify=verify,
    )
    with TestClient(app) as client:
        for user, url, channel in [("alice", alice_url, "789"), ("bob", bob_url, "456")]:
            headers = auth("google-" + user)
            response = client.post(
                f"/v1/web/admin/users/{user}/notifications",
                headers=auth(),
                json={"webhook_url": url},
            )
            assert response.status_code == 200
            assert response.json()["channel_id"] == channel
            assert response.headers["cache-control"] == "no-store"
            status = client.get("/v1/web/notifications", headers=headers)
            assert status.json()["channel_id"] == channel
            for secret in (alice_url, bob_url, "secret_ref", "webhook_url"):
                assert secret not in response.text + status.text
        users = client.get("/v1/web/admin/users", headers=auth()).json()
        assert users["notification_registration_available"]
        assert {u["id"]: u["notification"]["channel_id"] for u in users["items"]} == {
            "alice": "789",
            "bob": "456",
        }
        audit = client.get("/v1/web/admin/audit", headers=auth()).json()
        assert audit["total"] == 2
        assert all(
            r["actor"] == "alice" and r["action"] == "notifications.update" for r in audit["items"]
        )
        assert "FAKE_" not in json.dumps(audit) + json.dumps(users)
        alice_job = rig.submit(cpus=1, memory_mib=256)
        bob_job = rig.submit(user="bob", cpus=1, memory_mib=256)
        for job in (old_job, alice_job, bob_job):
            rig.event(job)
        # Registration and delivery still work after loading a new notifier from disk.
        reopened = Destinations(
            tmp_path / "fake-secrets.json",
            "999",
            destinations.client,
            managed_path=destinations.managed_path,
        )
        assert destinations.managed_path.stat().st_mode & 0o777 == 0o600
        notifier = Notifier(rig.hub, reopened)
        while notifier.step():
            pass
        delivered = {
            json.loads(r.content)["embeds"][0]["fields"][0]["value"]: r.url.path
            for r in calls
            if r.method == "POST"
        }
        assert "/111/" in delivered[old_job["id"]]
        assert "/333/" in delivered[alice_job["id"]]
        assert "/222/" in delivered[bob_job["id"]]
        assert all(row["state"] == "SENT" for row in records(rig))


def test_web_channel_registration_rejects_other_owners_and_untrusted_input(
    rig, transport, tmp_path
):
    destinations, calls, _ = transport
    destinations.managed_path = tmp_path / "discord-users.json"
    webhook = "https://discord.com/api/webhooks/222/FAKE_TEST_ONLY"
    app = create_app(
        rig.hub,
        destinations,
        background=False,
        web_config=settings(admin_users=["alice"]),
        web_verify=verify,
    )
    with TestClient(app) as client:
        for headers in ({}, auth(rig.alice), auth(rig.admin)):
            assert client.get("/v1/web/notifications", headers=headers).status_code == 401
            assert (
                client.post(
                    "/v1/web/admin/users/alice/notifications",
                    headers=headers,
                    json={"webhook_url": webhook},
                ).status_code
                == 401
            )
        assert not calls
        for user in ("alice", "bob"):
            assert (
                client.post(
                    f"/v1/web/admin/users/{user}/notifications",
                    headers=auth("google-bob"),
                    json={"webhook_url": webhook},
                ).status_code
                == 403
            )
        assert (
            client.post(
                "/v1/web/notifications", headers=auth("google-bob"), json={"webhook_url": webhook}
            ).status_code
            == 405
        )
        assert (
            client.post(
                "/v1/web/admin/users/missing/notifications",
                headers=auth(),
                json={"webhook_url": webhook},
            ).status_code
            == 404
        )
        assert not calls
        for body in (
            {"webhook_url": "http://127.0.0.1/private"},
            {"webhook_url": "https://discord.com.evil.test/api/webhooks/222/SECRET"},
            {"webhook_url": webhook + "?redirect=SECRET"},
            {"webhook_url": webhook, "user_id": "bob"},
            {"webhook_url": "SECRET" * 100},
        ):
            response = client.post(
                "/v1/web/admin/users/alice/notifications", headers=auth(), json=body
            )
            assert response.status_code == 422
            assert "SECRET" not in response.text and "FAKE_TEST_ONLY" not in response.text
        assert not calls
        response = client.post(
            "/v1/web/admin/users/alice/notifications", headers=auth(), json={"webhook_url": webhook}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DESTINATION_IN_USE"
        destinations.guild_id = "wrong-guild"
        response = client.post(
            "/v1/web/admin/users/bob/notifications", headers=auth(), json={"webhook_url": webhook}
        )
        assert response.json()["error"]["code"] == "DESTINATION_GUILD_NOT_ALLOWED"
        assert not destinations.managed_path.exists()
        assert rig.hub.notification_status("alice")["channel_id"] == "123"
        assert rig.hub.notification_status("bob")["channel_id"] == "456"


def test_web_registration_failure_preserves_previous_destination(
    rig, transport, tmp_path, monkeypatch
):
    destinations, _, _ = transport
    destinations.managed_path = tmp_path / "discord-users.json"
    webhook = "https://discord.com/api/webhooks/333/FAKE_PRIVATE_SECRET"
    before = rig.hub.notification_status("alice")

    def fail_write(*args):
        raise OSError("Simulated rename failure")

    def fail_request(request):
        raise httpx.ConnectError(f"Connection failed: {request.url}")

    app = create_app(
        rig.hub,
        destinations,
        background=False,
        web_config=settings(admin_users=["alice"]),
        web_verify=verify,
    )
    with TestClient(app) as client:
        monkeypatch.setattr("cowork_hub.notifications.os.replace", fail_write)
        response = client.post(
            "/v1/web/admin/users/alice/notifications", headers=auth(), json={"webhook_url": webhook}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DESTINATION_STORE_FAILED"
        assert not list(tmp_path.glob(".discord-*"))
        assert not destinations.managed_path.exists()
        assert all(entry["url"] != webhook for entry in destinations.entries.values())
        destinations.client.close()
        destinations.client = httpx.Client(transport=httpx.MockTransport(fail_request))
        response = client.post(
            "/v1/web/admin/users/alice/notifications", headers=auth(), json={"webhook_url": webhook}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DESTINATION_CHECK_FAILED"
        assert "FAKE_PRIVATE_SECRET" not in response.text
        assert rig.hub.notification_status("alice") == before


def test_unconfigured_user_never_falls_back_to_another_users_channel(rig, transport):
    destinations, calls, _ = transport
    with rig.hub.store.transaction() as db:
        db.execute("DELETE FROM notification_destinations WHERE user_id='bob'")
    app = create_app(
        rig.hub,
        destinations,
        background=False,
        web_config=settings(admin_users=["alice"]),
        web_verify=verify,
    )
    with TestClient(app) as client:
        status = client.get("/v1/web/notifications", headers=auth("google-bob")).json()
        assert status == {"configured": False}
        with pytest.raises(Error) as caught:
            rig.submit(user="bob")
        assert caught.value.code == "NOTIFICATION_NOT_CONFIGURED"
        assert not calls


def test_concurrent_registrations_preserve_both_users_and_private_file(transport, tmp_path):
    destinations, _, _ = transport
    destinations.managed_path = tmp_path / "discord-users.json"
    urls = [f"https://discord.com/api/webhooks/{number}/FAKE_ONLY" for number in (333, 444)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(destinations.register, ["alice", "bob"], urls))
    stored = json.loads(destinations.managed_path.read_text())
    assert set(stored) == {ref for ref, _ in results}
    assert {entry["user_id"] for entry in stored.values()} == {"alice", "bob"}
    assert destinations.register("alice", urls[0]) == results[0]
    assert json.loads(destinations.managed_path.read_text()) == stored
    destinations.managed_path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        Destinations(None, "999", destinations.client, managed_path=destinations.managed_path)
