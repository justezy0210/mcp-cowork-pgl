import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from test_notifications import transport as transport
from test_onboarding import DETAILS
from test_web import auth, settings, verify

from cowork_hub.api import create_app
from cowork_hub.models import Error
from cowork_hub.notifications import EnrollmentNotifier
from cowork_hub.onboarding import Enrollment, EnrollmentReview, Onboarding
from cowork_hub.service import Hub
from cowork_hub.store import SCHEMA_VERSION

IDENTITY = {"firebase_uid": "google-carol", "email": "carol@example.com"}


@pytest.fixture
def enrollment(rig, transport, tmp_path):
    destinations, calls, replies = transport
    destinations.managed_path = tmp_path / "managed.json"
    config = settings(admin_users=["alice"])
    onboarding = Onboarding(rig.hub, config, destinations)
    return onboarding, EnrollmentNotifier(rig.hub, destinations, config), calls, replies


def rows(rig):
    with rig.hub.store.transaction(write=False) as db:
        return [
            dict(row) for row in db.execute("SELECT * FROM enrollment_notifications ORDER BY id")
        ]


def submit(onboarding):
    return onboarding.submit(IDENTITY, Enrollment.model_validate(DETAILS))


def test_api_enqueues_once_and_delivers_only_to_administrator(rig, enrollment):
    onboarding, notifier, calls, _ = enrollment
    with TestClient(
        create_app(
            rig.hub,
            onboarding.destinations,
            background=False,
            web_config=onboarding.config,
            web_verify=verify,
        )
    ) as client:
        assert client.post("/v1/web/onboarding", json=DETAILS).status_code == 401
        assert rows(rig) == []
        for _ in range(2):
            response = client.post("/v1/web/onboarding", headers=auth("google-carol"), json=DETAILS)
            assert response.status_code == 201
        assert len(calls) == 1 and calls[0].method == "GET"
        queued = rows(rig)
        assert len(queued) == 1 and queued[0]["user_id"] == "alice"
        assert queued[0]["enrollment_id"] == response.json()["id"]
        assert notifier.step()
        assert not notifier.step()
        assert calls[-1].url.path == "/api/webhooks/111/FAKE_TEST_ONLY"
        assert calls[-1].url.params["wait"] == "true"
        payload = json.loads(calls[-1].content)
        assert payload["allowed_mentions"] == {"parse": []}
        assert payload["embeds"][0]["url"] == "https://lab-test.web.app/#admin"
        assert payload["embeds"][0]["title"] == "새 계정 승인 요청"
        assert "carol" in json.dumps(payload) and "1200 / 1000" in json.dumps(payload)
        for private in ("FAKE_ENROLLMENT_ONLY", "google-carol", "carol@example.com", "secret_ref"):
            assert private not in json.dumps(queued) and private not in json.dumps(payload)
        assert rows(rig)[0]["state"] == "SENT"


def test_request_and_alert_roll_back_together(rig, enrollment, monkeypatch):
    onboarding, _, _, _ = enrollment
    from cowork_hub import onboarding as module

    original = module.enqueue_enrollment

    def fail_after_enqueue(*args):
        original(*args)
        raise Error("TEST_FAILURE", "Test rollback")

    monkeypatch.setattr(module, "enqueue_enrollment", fail_after_enqueue)
    with pytest.raises(Error, match="Test rollback"):
        submit(onboarding)
    with rig.hub.store.transaction(write=False) as db:
        assert not db.execute("SELECT 1 FROM web_enrollments").fetchone()
    assert rows(rig) == []


def test_application_background_loop_delivers_enrollment_alert(rig, enrollment):
    onboarding, _, calls, _ = enrollment
    with TestClient(
        create_app(
            rig.hub, onboarding.destinations, web_config=onboarding.config, web_verify=verify
        )
    ) as client:
        assert (
            client.post(
                "/v1/web/onboarding", headers=auth("google-carol"), json=DETAILS
            ).status_code
            == 201
        )
        deadline = time.monotonic() + 5
        while rows(rig)[0]["state"] != "SENT" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert rows(rig)[0]["state"] == "SENT"
        assert len([call for call in calls if call.method == "POST"]) == 1


def test_concurrent_notifiers_claim_one_delivery(rig, enrollment):
    onboarding, notifier, calls, _ = enrollment
    submit(onboarding)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: notifier.step(), range(2))) == [False, True]
    assert rows(rig)[0]["state"] == "SENT"
    assert len([call for call in calls if call.method == "POST"]) == 1


def test_transient_failure_and_rate_limit_retry_survive_restart(rig, enrollment):
    onboarding, notifier, calls, replies = enrollment
    request = submit(onboarding)
    replies.extend([httpx.Response(503), httpx.Response(429, json={"retry_after": 10})])
    assert notifier.step()
    assert rows(rig)[0]["error_code"] == "DISCORD_UNAVAILABLE"
    assert onboarding.status(IDENTITY)["id"] == request["id"]
    assert not notifier.step()
    rig.now += 2
    restarted = EnrollmentNotifier(
        Hub(rig.reopen_store(), clock=lambda: rig.now), onboarding.destinations, onboarding.config
    )
    assert restarted.step()
    assert rows(rig)[0]["next_attempt"] == rig.now + 10
    rig.now += 9
    assert not restarted.step()
    rig.now += 1
    assert restarted.step()
    assert rows(rig)[0]["state"] == "SENT" and rows(rig)[0]["attempts"] == 3
    assert len([call for call in calls if call.method == "POST"]) == 3


def test_abandoned_delivery_lease_is_recovered(rig, enrollment):
    onboarding, notifier, _, _ = enrollment
    submit(onboarding)
    with rig.hub.store.transaction() as db:
        db.execute(
            "UPDATE enrollment_notifications SET state='SENDING',lease_until=?", (rig.now + 60,)
        )
    assert not notifier.step()
    rig.now += 61
    assert notifier.step()
    assert rows(rig)[0]["state"] == "SENT"


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
def test_reviewed_requests_do_not_send_stale_alerts(rig, enrollment, decision):
    onboarding, notifier, calls, _ = enrollment
    request = submit(onboarding)
    onboarding.review(
        "alice", request["id"], EnrollmentReview(decision=decision, allowed_nodes=["A"])
    )
    assert notifier.step()
    assert rows(rig)[0]["state"] == "SUPPRESSED"
    assert all(call.method == "GET" for call in calls)


def test_rejected_resubmission_gets_one_new_alert(rig, enrollment):
    onboarding, notifier, calls, _ = enrollment
    previous = submit(onboarding)
    onboarding.review("alice", previous["id"], EnrollmentReview(decision="REJECTED"))
    current = submit(onboarding)
    assert previous["id"] != current["id"]
    assert submit(onboarding) == current
    assert notifier.step() and notifier.step() and not notifier.step()
    notifications = {row["enrollment_id"]: row for row in rows(rig)}
    assert notifications[previous["id"]]["state"] == "SUPPRESSED"
    assert notifications[current["id"]]["state"] == "SENT"
    assert len([call for call in calls if call.method == "POST"]) == 1


@pytest.mark.parametrize("unavailable", ["missing", "disabled", "wrong_owner"])
def test_missing_admin_route_waits_and_uses_corrected_route(rig, enrollment, unavailable):
    onboarding, notifier, calls, _ = enrollment
    with rig.hub.store.transaction() as db:
        if unavailable == "missing":
            db.execute("DELETE FROM notification_destinations WHERE user_id='alice'")
        elif unavailable == "disabled":
            db.execute("UPDATE notification_destinations SET enabled=0 WHERE user_id='alice'")
        else:
            db.execute(
                "UPDATE notification_destinations SET secret_ref='bob-ref',channel_id='456' WHERE user_id='alice'"
            )
    submit(onboarding)
    assert notifier.step()
    assert rows(rig)[0]["state"] == "PENDING"
    assert rows(rig)[0]["error_code"] == "DESTINATION_UNAVAILABLE"
    assert all(call.method == "GET" for call in calls)
    rig.hub.provision_destination("alice", "alice-ref", "123")
    rig.now += 60
    assert notifier.step()
    assert rows(rig)[0]["state"] == "SENT"
    assert calls[-1].url.path == "/api/webhooks/111/FAKE_TEST_ONLY"


@pytest.mark.parametrize("revocation", ["disabled", "removed"])
def test_admin_access_rechecked_before_delivery(rig, enrollment, revocation):
    onboarding, notifier, calls, _ = enrollment
    submit(onboarding)
    if revocation == "disabled":
        with rig.hub.store.transaction() as db:
            db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
    else:
        onboarding.config.admin_users = []
    assert notifier.step()
    assert rows(rig)[0]["state"] == "SUPPRESSED"
    assert all(call.method == "GET" for call in calls)


def test_each_configured_admin_receives_one_alert(rig, enrollment):
    onboarding, notifier, calls, _ = enrollment
    onboarding.config.admin_users = ["alice", "bob", "alice", "unconfigured"]
    submit(onboarding)
    assert {row["user_id"] for row in rows(rig)} == {"alice", "bob"}
    assert notifier.step() and notifier.step() and not notifier.step()
    assert {call.url.path for call in calls if call.method == "POST"} == {
        "/api/webhooks/111/FAKE_TEST_ONLY",
        "/api/webhooks/222/FAKE_TEST_ONLY",
    }


def test_permanent_discord_failure_is_recorded_without_losing_application(rig, enrollment):
    onboarding, notifier, _, replies = enrollment
    request = submit(onboarding)
    replies.append(httpx.Response(404))
    assert notifier.step()
    assert rows(rig)[0]["state"] == "FAILED"
    assert rows(rig)[0]["error_code"] == "DISCORD_HTTP_404"
    assert onboarding.status(IDENTITY)["id"] == request["id"]


def test_schema_six_upgrade_preserves_jobs_and_credentials(rig):
    job = rig.submit()
    with rig.hub.store.transaction() as db:
        db.execute("DROP TABLE enrollment_notifications")
        db.execute(
            "PRAGMA user_version=6"
            if rig.hub.store.backend == "sqlite"
            else "UPDATE hub_schema SET version=6 WHERE id=1"
        )
    rig.hub.store = rig.reopen_store()
    assert rig.hub.authenticate(rig.alice)["id"] == "alice"
    assert rig.hub.get_job("alice", job["id"])["execution_id"] == job["execution_id"]
    assert rows(rig) == []
    with rig.hub.store.transaction(write=False) as db:
        version = db.execute(
            "PRAGMA user_version"
            if rig.hub.store.backend == "sqlite"
            else "SELECT version FROM hub_schema WHERE id=1"
        ).fetchone()[0]
    assert version == SCHEMA_VERSION
