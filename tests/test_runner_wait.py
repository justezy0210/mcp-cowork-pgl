"""Long waits must be quiet, job-scoped, and independent of process supervision."""

import json
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_local_runner import (
    local_env,
    report,
    runner,
    submission,
    wait_state,
)
from test_local_runner import (
    runtime as runtime_fixture,
)

from cowork_hub.api import create_app
from cowork_hub.models import Error
from cowork_hub.runner import read_private

runtime = runtime_fixture


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("Condition did not become true before the deadline")


def pair(rig):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    first = hub.submit("alice", submission(env), runner=runner())
    second = hub.submit("alice", submission(env, "request-2"), runner=runner(name="runner-2"))
    return first, second


def test_heartbeat_does_not_reschedule_or_write_unchanged_liveness(rig, monkeypatch):
    hub = rig.hub
    first, second = pair(rig)
    statements = []
    original = hub.store.transaction

    class Spy:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, args=()):
            statements.append(sql)
            return self.db.execute(sql, args)

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as db:
            yield Spy(db)

    monkeypatch.setattr(hub.store, "transaction", transaction)

    def unexpected_schedule(*args):
        pytest.fail("A routine heartbeat scanned the queue")

    monkeypatch.setattr(hub, "_schedule", unexpected_schedule)
    revision = hub.local.poll("alice", second["id"], runner(name="runner-2"))["revision"]
    for _ in range(9):
        rig.now += 1
        assert (
            hub.local.poll("alice", second["id"], runner(name="runner-2"))["revision"] == revision
        )
    assert not any(sql.startswith("UPDATE") for sql in statements)
    rig.now += 1
    hub.local.poll("alice", second["id"], runner(name="runner-2"))
    assert sum(sql.startswith("UPDATE local_runs") for sql in statements) == 1


def test_idle_ticks_publish_nothing_and_committed_handoff_publishes_both_jobs(rig):
    first, second = pair(rig)
    notifications = []

    def observed(ids):
        # A separate read here can see the transition: publication is after commit.
        notifications.append({job_id: rig.hub.get_job("alice", job_id)["state"] for job_id in ids})

    rig.hub.on_jobs_changed = observed
    for _ in range(3):
        rig.hub.tick()
    assert notifications == []
    report(rig.hub, first, runner(), kind="not_started", code=None)
    assert notifications == [{first["id"]: "FAILED", second["id"]: "DISPATCHING"}]


def test_wait_ignores_heartbeats_ticks_and_other_jobs_then_wakes_on_cancel(rig, monkeypatch):
    hub = rig.hub
    first, second = pair(rig)
    body = runner(name="runner-2").model_dump()
    headers = {"Authorization": "Bearer " + rig.alice}
    url = f"/v1/local/jobs/{second['id']}/wait"
    reads = []
    original = hub.local.read

    def read(*args):
        result = original(*args)
        reads.append(args[1])
        return result

    monkeypatch.setattr(hub.local, "read", read)
    with TestClient(create_app(hub, background=False)) as client, ThreadPoolExecutor() as pool:
        initial = client.post(url, headers=headers, json=body).json()
        reads.clear()
        pending = pool.submit(
            client.post,
            url,
            headers=headers,
            json=body,
            params={"since": initial["revision"], "wait_seconds": 2},
        )
        until(lambda: len(reads) == 1)
        # These transactions also prove that waiting holds no database write lock.
        hub.tick()
        hub.local.poll("alice", second["id"], runner(name="runner-2"))
        hub.local.claim("alice", first["id"], runner())
        report(hub, first, runner(), kind="started", code=None)
        time.sleep(0.15)
        assert not pending.done()
        assert reads == [second["id"]]
        started = time.monotonic()
        hub.cancel("alice", second["id"])
        result = pending.result(timeout=1).json()
        assert result["state"] == "CANCELLED"
        assert time.monotonic() - started < 1
        assert len(reads) == 2
        assert (
            client.post(url, headers={"Authorization": "Bearer " + rig.bob}, json=body).status_code
            == 404
        )
        assert client.post(url, headers=headers, json=runner().model_dump()).status_code == 404
        assert (
            client.post(url, headers=headers, json=body, params={"wait_seconds": 21}).status_code
            == 422
        )


def test_transition_during_first_read_is_not_lost_and_old_cursor_survives_restart(rig, monkeypatch):
    hub = rig.hub
    first, second = pair(rig)
    identity = runner(name="runner-2")
    initial = hub.local.read("alice", second["id"], identity)
    original = hub.local.read
    raced = False

    def read(*args):
        nonlocal raced
        result = original(*args)
        if not raced:
            raced = True
            report(hub, first, runner(), kind="not_started", code=None)
        return result

    monkeypatch.setattr(hub.local, "read", read)
    url = f"/v1/local/jobs/{second['id']}/wait"
    kwargs = dict(
        headers={"Authorization": "Bearer " + rig.alice},
        json=identity.model_dump(),
        params={"since": initial["revision"], "wait_seconds": 2},
    )
    with TestClient(create_app(hub, background=False)) as client:
        started = time.monotonic()
        assert client.post(url, **kwargs).json()["state"] == "DISPATCHING"
        assert time.monotonic() - started < 1
    with TestClient(create_app(hub, background=False)) as restarted:
        started = time.monotonic()
        assert restarted.post(url, **kwargs).json()["state"] == "DISPATCHING"
        assert time.monotonic() - started < 1


def test_wait_timeout_reads_only_at_entry_and_deadline(rig, monkeypatch):
    _, job = pair(rig)
    hub = rig.hub
    body = runner(name="runner-2")
    initial = hub.local.read("alice", job["id"], body)
    calls = []
    original = hub.local.read

    def read(*args):
        calls.append(time.monotonic())
        return original(*args)

    monkeypatch.setattr(hub.local, "read", read)
    with TestClient(create_app(hub, background=False)) as client:
        result = client.post(
            f"/v1/local/jobs/{job['id']}/wait",
            headers={"Authorization": "Bearer " + rig.alice},
            json=body.model_dump(),
            params={"since": initial["revision"], "wait_seconds": 0.3},
        )
    assert result.json()["revision"] == initial["revision"]
    assert len(calls) == 2
    assert 0.25 <= calls[1] - calls[0] < 1


def test_queued_runner_is_quiet_across_heartbeat_and_wait_timeout(runtime, monkeypatch):
    hub, _, start = runtime
    counts = {"poll": 0, "read": 0}
    tracked = []
    for method in counts:
        original = getattr(hub.local, method)

        def wrapped(*args, _method=method, _original=original):
            if tracked and args[1] == tracked[0]:
                counts[_method] += 1
            return _original(*args)

        monkeypatch.setattr(hub.local, method, wrapped)
    first = start("import time; time.sleep(100)")
    wait_state(hub, first, "RUNNING")
    second = start("pass")
    tracked.append(second["job_id"])
    time.sleep(0.5)  # initial status is durably recorded before measuring idle activity
    record = Path(second["record"])
    before = record.stat().st_mtime_ns
    counts.update(poll=0, read=0)
    time.sleep(21.5)
    assert hub.get_job("tester", second["job_id"])["state"] == "QUEUED"
    assert record.stat().st_mtime_ns == before
    assert counts["poll"] <= 3  # two ten-second heartbeats, instead of about 21 polls
    assert counts["read"] <= 3  # timeout and next wait entry, no per-second DB reads
    print(
        "quiet_runner_measurement=" + json.dumps({"seconds": 21.5, **counts, "journal_writes": 0})
    )
    released_at = time.monotonic()
    hub.cancel("tester", first["job_id"])
    wait_state(hub, first, "CANCELLED", timeout=2)
    wait_state(hub, second, "SUCCEEDED", timeout=2)
    assert time.monotonic() - released_at < 3


def test_slow_control_response_does_not_delay_local_completion_journal(runtime, monkeypatch):
    hub, _, start = runtime
    entered = threading.Event()
    release = threading.Event()
    original = hub.local.event

    def slow(*args):
        result = original(*args)
        if args[2].event.kind == "started":
            entered.set()
            release.wait(timeout=2.5)
        return result

    monkeypatch.setattr(hub.local, "event", slow)
    job = start("import time; time.sleep(0.4)")
    try:
        assert entered.wait(timeout=2)
        until(lambda: json.loads(read_private(job["record"]))["phase"] == "REPORTING", timeout=1.2)
        assert not release.is_set()
        state = json.loads(read_private(job["record"]))
        assert [e["kind"] for e in state["events"]] == ["started", "succeeded"]
    finally:
        release.set()
    wait_state(hub, job, "SUCCEEDED")


def test_wait_failures_back_off_and_local_signal_still_kills_stubborn_child(
    runtime, monkeypatch, tmp_path
):
    hub, _, start = runtime
    failures = []

    def unavailable(*args):
        failures.append(time.monotonic())
        raise Error("TEMPORARY", "simulated wait outage", 503)

    monkeypatch.setattr(hub.local, "read", unavailable)
    ready = tmp_path / "signal-ready"
    job = start(
        "import signal,time,pathlib,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).touch(); time.sleep(100)",
        str(ready),
    )
    wait_state(hub, job, "RUNNING")
    until(ready.exists)
    until(lambda: len(failures) >= 1)
    time.sleep(0.2)
    record = Path(job["record"])
    mtime = record.stat().st_mtime_ns
    time.sleep(3)
    assert 2 <= len(failures) <= 4
    assert record.stat().st_mtime_ns == mtime
    state = json.loads(read_private(record))
    begin = time.monotonic()
    os.kill(state["supervisor_pid"], signal.SIGTERM)
    wait_state(hub, job, "CANCELLED", timeout=5)
    assert 2.8 <= time.monotonic() - begin < 5


def test_delayed_queued_reply_cannot_erase_claimed_execution(runtime, monkeypatch, tmp_path):
    hub, _, start = runtime
    hub.heartbeat_timeout = 6  # two-second heartbeat for this bounded race test
    snapshot_taken = threading.Event()
    release_reply = threading.Event()
    finish = tmp_path / "finish-after-delayed-reply"
    original = hub.local.read

    def delayed(*args):
        snapshot = original(*args)
        if snapshot["state"] == "QUEUED" and not snapshot_taken.is_set():
            # A changed queued reason makes this a returnable snapshot, rather than
            # letting /wait re-read the now-running job after this delayed read.
            snapshot["reason"] = "RUNNER_OFFLINE"
            snapshot["revision"] = "0" * 64
            snapshot_taken.set()
            release_reply.wait(timeout=8)
        return snapshot

    monkeypatch.setattr(hub.local, "read", delayed)
    first = start("import time; time.sleep(100)")
    wait_state(hub, first, "RUNNING")
    second = start(
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "exec('while not p.exists(): time.sleep(.02)')",
        str(finish),
    )
    try:
        assert snapshot_taken.wait(timeout=2)
        hub.cancel("tester", first["job_id"])
        running = wait_state(hub, second, "RUNNING", timeout=4)
        release_reply.set()
        time.sleep(0.3)
        assert json.loads(read_private(second["record"]))["execution_id"] == running["execution_id"]
        finish.touch()
        wait_state(hub, second, "SUCCEEDED", timeout=2)
    finally:
        release_reply.set()
        finish.touch()


def test_wait_delivers_cancel_even_while_heartbeat_retries_back_off(runtime, monkeypatch):
    hub, _, start = runtime
    hub.heartbeat_timeout = 6
    failures = []
    enabled = threading.Event()
    original = hub.local.poll

    def failing(*args):
        if enabled.is_set():
            failures.append(time.monotonic())
            raise Error("TEMPORARY", "heartbeat unavailable but wait connection works", 503)
        return original(*args)

    monkeypatch.setattr(hub.local, "poll", failing)
    job = start("import time; time.sleep(100)")
    wait_state(hub, job, "RUNNING")
    time.sleep(0.2)
    enabled.set()
    until(lambda: len(failures) >= 2, timeout=5)
    started = time.monotonic()
    hub.cancel("tester", job["job_id"])
    wait_state(hub, job, "CANCELLED", timeout=0.9)
    assert time.monotonic() - started < 0.9


def test_cancel_seen_during_claim_prevents_spawning_command(runtime, monkeypatch, tmp_path):
    hub, _, start = runtime
    hub.heartbeat_timeout = 6
    waiting = threading.Event()
    release_wait = threading.Event()
    claimed = threading.Event()
    release_claim = threading.Event()
    original_read = hub.local.read
    original_claim = hub.local.claim
    tracked = []
    marker = tmp_path / "must-not-spawn-after-cancel"

    def read(*args):
        snapshot = original_read(*args)
        if snapshot["state"] == "QUEUED" and not waiting.is_set():
            tracked.append(args[1])
            waiting.set()
            release_wait.wait(timeout=8)
            return original_read(*args)
        return snapshot

    def claim(*args):
        result = original_claim(*args)
        if tracked and args[1] == tracked[0]:
            claimed.set()
            release_claim.wait(timeout=2.5)
        return result

    monkeypatch.setattr(hub.local, "read", read)
    monkeypatch.setattr(hub.local, "claim", claim)
    first = start("import time; time.sleep(100)")
    wait_state(hub, first, "RUNNING")
    second = start("import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker))
    try:
        assert waiting.wait(timeout=2)
        hub.cancel("tester", first["job_id"])
        assert claimed.wait(timeout=4)
        hub.cancel("tester", second["job_id"])
        release_wait.set()
        # The control response is still held, so collect_watch must consume cancellation.
        until(lambda: json.loads(read_private(second["record"])).get("hub_state") == "DISPATCHING")
        time.sleep(0.3)
        release_claim.set()
        done = wait_state(hub, second, "CANCELLED", timeout=2)
        assert not marker.exists()
        assert done["started_at"] is None
        assert done["reason"] == "NOT_STARTED"
    finally:
        release_wait.set()
        release_claim.set()


def test_new_runner_falls_back_on_hub_without_wait_capability(request, monkeypatch):
    import test_local_runner as local_tests

    original = local_tests.create_app

    def old_hub(*args, **kwargs):
        app = original(*args, **kwargs)
        for route in app.routes:
            if getattr(route, "path", None) == "/v1/capabilities":
                route.dependant.call = lambda: {"local_runner": 1}
        return app

    monkeypatch.setattr(local_tests, "create_app", old_hub)
    hub, _, start = request.getfixturevalue("runtime")
    polls = []
    poll = hub.local.poll

    def observe_poll(*args):
        polls.append(time.monotonic())
        return poll(*args)

    def no_wait(*args):
        pytest.fail("Old hub fallback must not use the wait endpoint")

    monkeypatch.setattr(hub.local, "poll", observe_poll)
    monkeypatch.setattr(hub.local, "read", no_wait)
    job = start("import time; time.sleep(2.3)")
    wait_state(hub, job, "SUCCEEDED", timeout=4)
    assert len(polls) >= 2
