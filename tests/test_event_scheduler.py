"""Event scheduling, expiration deadlines, and restart/fallback recovery."""

import asyncio
import contextlib
import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_local_runner import local_env, report, runner, submission
from test_runner_wait import pair, until

from cowork_hub.api import create_app
from cowork_hub.models import Heartbeat
from cowork_hub.scheduling import run_scheduler


def count_calls(hub, monkeypatch):
    counts = {"tick": 0, "expire": 0, "next_liveness_deadline": 0, "_schedule": 0}
    for name in counts:
        original = getattr(hub, name)

        def wrapped(*args, _name=name, _original=original):
            counts[_name] += 1
            return _original(*args)

        monkeypatch.setattr(hub, name, wrapped)
    return counts


def real_clock(rig, timeout):
    start = time.monotonic()
    base = rig.now
    rig.hub.clock = lambda: base + time.monotonic() - start
    rig.hub.heartbeat_timeout = timeout


def test_deadlines_expire_once_at_boundary_and_unknown_keeps_reservations(rig):
    hub = rig.hub
    first, queued = pair(rig)
    base = rig.now
    assert hub.next_liveness_deadline() == base + 30
    rig.now += 10
    hub.local.poll("alice", first["id"], runner())
    rig.now = base + 30
    hub.expire()
    assert hub.get_job("alice", queued["id"])["reason"] == "RUNNER_OFFLINE"
    assert hub.get_job("alice", first["id"])["state"] == "DISPATCHING"
    assert hub.next_liveness_deadline() == base + 40
    rig.now = base + 40
    hub.expire()
    assert hub.get_job("alice", first["id"])["state"] == "UNKNOWN"
    assert hub.cluster("alice")[0]["reserved_cpus"] == 8
    assert hub.next_liveness_deadline() is None
    # A returning queued runner acquires a new deadline even if it still cannot start.
    hub.local.poll("alice", queued["id"], runner(name="runner-2"))
    assert hub.get_job("alice", queued["id"])["reason"] != "RUNNER_OFFLINE"
    assert hub.next_liveness_deadline() == base + 70
    report(hub, first, runner(), kind="not_started", code=None)
    assert hub.get_job("alice", queued["id"])["state"] == "DISPATCHING"


def test_worker_deadline_is_extended_without_rescanning_queue(rig, monkeypatch):
    hub = rig.hub
    first = rig.submit()
    rig.submit()
    deadline = hub.next_liveness_deadline()
    calls = count_calls(hub, monkeypatch)
    rig.now += 10
    rig.heartbeat()
    assert hub.next_liveness_deadline() == deadline + 10
    assert calls["_schedule"] == 0
    rig.now += 30
    hub.expire()
    assert hub.get_job("alice", first["id"])["state"] == "UNKNOWN"
    assert hub.next_liveness_deadline() is None
    assert calls["_schedule"] == 0
    assert hub.cluster("alice")[0]["reserved_cpus"] == 8


def test_environment_change_and_returning_worker_schedule_without_tick(rig, monkeypatch):
    hub = rig.hub
    hub.heartbeat("A", Heartbeat(environments=[]))
    queued = rig.submit()
    assert queued["state"] == "QUEUED"
    calls = count_calls(hub, monkeypatch)
    rig.heartbeat()
    assert rig.state(queued) == "DISPATCHING"
    assert calls["_schedule"] == 1
    # Recovering a silent node also reconsiders jobs when environment status is unchanged.
    rig.now += 31
    hub.expire()
    report_before = hub.next_liveness_deadline()
    assert report_before is None
    rig.event(hub.get_job("alice", queued["id"]), "not_started")
    other = rig.submit()
    assert other["state"] == "QUEUED"
    before = calls["_schedule"]
    rig.heartbeat()
    assert rig.state(other) == "DISPATCHING"
    assert calls["_schedule"] == before + 1
    assert calls["tick"] == 0


def test_idle_background_does_not_read_database_every_second(rig, monkeypatch):
    calls = count_calls(rig.hub, monkeypatch)
    with TestClient(create_app(rig.hub)):
        until(lambda: calls["next_liveness_deadline"] >= 1)
        time.sleep(2.2)
        assert calls == {"tick": 1, "expire": 0, "next_liveness_deadline": 1, "_schedule": 1}
        print("quiet_scheduler_measurement=" + str({"seconds": 2.2, **calls}))


def test_expiration_timer_never_scans_or_releases_the_queue(rig, monkeypatch):
    first, queued = pair(rig)
    real_clock(rig, timeout=0.8)
    calls = count_calls(rig.hub, monkeypatch)
    with TestClient(create_app(rig.hub)):
        until(lambda: rig.state(first) == "UNKNOWN", timeout=2)
        assert rig.hub.get_job("alice", queued["id"])["reason"] == "RUNNER_OFFLINE"
        until(lambda: calls["next_liveness_deadline"] >= 2)
        before = dict(calls)
        time.sleep(0.2)
        assert calls == before  # expired timestamps cannot create a hot loop
        assert calls["tick"] == calls["_schedule"] == 1
        assert calls["expire"] == 1
        assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 8


def test_timer_rechecks_fresh_heartbeat_before_marking_a_job_offline(rig, monkeypatch):
    first, queued = pair(rig)
    real_clock(rig, timeout=1.2)
    calls = count_calls(rig.hub, monkeypatch)
    with TestClient(create_app(rig.hub)):
        until(lambda: calls["next_liveness_deadline"] >= 1)
        time.sleep(0.6)
        rig.hub.local.poll("alice", first["id"], runner())
        rig.hub.local.poll("alice", queued["id"], runner(name="runner-2"))
        until(lambda: calls["next_liveness_deadline"] >= 2, timeout=1)
        assert rig.state(first) == "DISPATCHING"
        assert rig.hub.get_job("alice", queued["id"])["reason"] != "RUNNER_OFFLINE"
        assert calls["expire"] == 0
        assert calls["tick"] == calls["_schedule"] == 1


def test_new_job_wakes_an_idle_timer_and_shutdown_is_prompt(rig, monkeypatch):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    real_clock(rig, timeout=0.8)
    calls = count_calls(hub, monkeypatch)
    with TestClient(create_app(hub)):
        until(lambda: calls["next_liveness_deadline"] >= 1)
        time.sleep(0.1)
        first = hub.submit("alice", submission(env), runner=runner())
        until(lambda: hub.get_job("alice", first["id"])["state"] == "UNKNOWN", timeout=2)
        assert calls["tick"] == 1
        end = time.monotonic()
    assert time.monotonic() - end < 1


def test_job_arriving_during_deadline_read_is_not_lost(rig, monkeypatch):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    original = hub.next_liveness_deadline
    read_started, release = threading.Event(), threading.Event()
    real_clock(rig, timeout=0.8)

    def paused():
        snapshot = original()
        if not read_started.is_set():
            read_started.set()
            release.wait(timeout=2)
        return snapshot

    monkeypatch.setattr(hub, "next_liveness_deadline", paused)
    with TestClient(create_app(hub)):
        try:
            assert read_started.wait(timeout=2)
            job = hub.submit("alice", submission(env), runner=runner())
            release.set()
            until(lambda: hub.get_job("alice", job["id"])["state"] == "UNKNOWN", timeout=2)
        finally:
            release.set()


def test_startup_reconciles_preexisting_queue_without_a_new_request(rig):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    # Existing submissions may be queued while an environment is unavailable.
    with hub.store.transaction() as db:
        db.execute("UPDATE environments SET status='UNAVAILABLE' WHERE id=?", (env,))
    job = hub.submit("alice", submission(env), runner=runner())
    assert job["state"] == "QUEUED"
    with hub.store.transaction() as db:
        db.execute("UPDATE environments SET status='READY' WHERE id=?", (env,))
    with TestClient(create_app(hub)):
        until(lambda: hub.get_job("alice", job["id"])["state"] == "DISPATCHING")


def test_periodic_reconcile_and_transient_database_failure_recover(rig, monkeypatch):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    with hub.store.transaction() as db:
        db.execute("UPDATE environments SET status='UNAVAILABLE' WHERE id=?", (env,))
    job = hub.submit("alice", submission(env), runner=runner())
    original = hub.next_liveness_deadline
    reads = []

    def transient():
        reads.append(time.monotonic())
        if len(reads) == 1:
            raise RuntimeError("simulated database outage")
        return original()

    monkeypatch.setattr(hub, "next_liveness_deadline", transient)

    async def exercise():
        task = asyncio.create_task(
            run_scheduler(hub, asyncio.Event(), reconcile_seconds=0.3, retry_seconds=0.05)
        )
        try:
            while len(reads) < 2:
                await asyncio.sleep(0.01)
            # Deliberately bypass the service notification to simulate a missed condition change.
            with hub.store.transaction() as db:
                db.execute("UPDATE environments SET status='READY' WHERE id=?", (env,))
            for _ in range(100):
                if hub.get_job("alice", job["id"])["state"] == "DISPATCHING":
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Fallback did not reconcile the queue")
            assert reads[1] - reads[0] >= 0.04
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(exercise())
