import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import (
    JobSubmit,
    LocalEnvironment,
    LocalEvent,
    NodeCreate,
    RunnerIdentity,
    UserCreate,
)
from cowork_hub.runner import launch, read_private, save
from cowork_hub.service import Hub
from cowork_hub.store import Store


def local_env(
    hub, user="alice", *, uid=1000, gid=1000, instance="local-alice", cpus=8, workdir="/work"
):
    return hub.local.register(
        user,
        LocalEnvironment(
            name=instance,
            node_id="A",
            ssh_target="local",
            workdir=workdir,
            instance_id=instance,
            uid=uid,
            gid=gid,
            cpus=cpus,
            memory_mib=256,
        ),
    )["id"]


def runner(instance="local-alice", name="runner-1", uid=1000, gid=1000):
    return RunnerIdentity(
        instance_id=instance, runner_id=name, uid=uid, gid=gid, claim_id="claim-1"
    )


def submission(env, key="request-1", cpus=8):
    return JobSubmit(
        request_key=key,
        spec={
            "name": "test",
            "environment_ids": [env],
            "argv": ["true"],
            "cpus": cpus,
            "memory_mib": 64,
        },
    )


def report(hub, job, identity, kind="succeeded", code=0, event_id="event-1"):
    return hub.local.event(
        "alice",
        job["id"],
        LocalEvent(
            runner=identity,
            event={
                "event_id": event_id,
                "execution_id": job["execution_id"],
                "kind": kind,
                "occurred_at": hub.clock(),
                "exit_code": code,
            },
        ),
    )


def test_local_scope_claim_and_atomic_release(rig):
    hub = rig.hub
    env = local_env(hub)

    def auth(token):
        return {"Authorization": "Bearer " + token}

    body = {**submission(env).model_dump(), "runner": runner().model_dump()}
    with TestClient(create_app(hub, background=False)) as client:
        assert client.post("/v1/local/jobs", headers=auth(rig.alice), json=body).status_code == 409
        approve = "/v1/admin/local/environments/" + env + "/approve"
        assert client.post(approve, headers=auth(rig.alice)).status_code == 403
        assert client.post(approve, headers=auth(rig.admin)).status_code == 200
        first = client.post("/v1/local/jobs", headers=auth(rig.alice), json=body).json()
        second = hub.submit("alice", submission(env, "request-2"), runner=runner(name="runner-2"))
        assert first["state"] == "DISPATCHING" and second["state"] == "QUEUED"
        prefix = "/v1/local/jobs/" + first["id"]
        assert (
            client.post(
                prefix + "/poll", headers=auth(rig.bob), json=runner().model_dump()
            ).status_code
            == 404
        )
        assert (
            client.post(
                prefix + "/poll", headers=auth(rig.alice), json=runner(name="wrong").model_dump()
            ).status_code
            == 404
        )
        assert client.post(
            prefix + "/claim", headers=auth(rig.alice), json=runner().model_dump()
        ).json()["granted"]
        assert (
            client.post(
                prefix + "/claim", headers=auth(rig.alice), json=runner().model_dump()
            ).status_code
            == 409
        )
        assert hub.assignments("A") == []
        assert not report(hub, first, runner())["duplicate"]
        assert report(hub, first, runner())["duplicate"]
        assert hub.get_job("alice", second["id"])["state"] == "DISPATCHING"
        assert hub.cluster("alice")[0]["reserved_cpus"] == 8
        assert (
            client.post(
                "/v1/jobs", headers=auth(rig.alice), json=submission(env, "generic-1").model_dump()
            ).status_code
            == 409
        )


def test_runner_liveness_is_per_job_and_stale_queued_jobs_do_not_reserve(rig):
    hub = rig.hub
    env = local_env(hub)
    hub.local.approve(env)
    first = hub.submit("alice", submission(env, cpus=4), runner=runner())
    second = hub.submit(
        "alice", submission(env, "request-2", cpus=4), runner=runner(name="runner-2")
    )
    queued = hub.submit(
        "alice", submission(env, "request-3", cpus=4), runner=runner(name="runner-3")
    )
    rig.now += 20
    hub.local.poll("alice", second["id"], runner(name="runner-2"))
    rig.now += 15
    hub.tick()
    assert hub.get_job("alice", first["id"])["state"] == "UNKNOWN"
    assert hub.get_job("alice", second["id"])["state"] == "DISPATCHING"
    report(hub, second, runner(name="runner-2"), kind="not_started", code=None)
    assert hub.get_job("alice", queued["id"])["state"] == "QUEUED"
    assert hub.cluster("alice")[0]["reserved_cpus"] == 4
    hub.local.poll("alice", queued["id"], runner(name="runner-3"))
    assert hub.get_job("alice", queued["id"])["state"] == "DISPATCHING"


def test_local_registration_idempotency_identity_grants_and_worker_isolation(rig):
    from cowork_hub.models import Error

    env = local_env(rig.hub)
    assert local_env(rig.hub) == env
    with pytest.raises(Error):
        local_env(rig.hub, uid=1001)
    rig.hub.local.approve(env)
    rig.heartbeat()
    assert (
        next(e for e in rig.hub.list_environments("alice") if e["id"] == env)["status"] == "READY"
    )
    first = rig.hub.submit("alice", submission(env), runner=runner())
    with pytest.raises(Error):
        rig.hub.submit("alice", submission(env), runner=runner(name="other"))
    with pytest.raises(Error):
        rig.event(first)
    rig.hub.set_grants("alice", [])
    with pytest.raises(Error):
        rig.hub.local.claim("alice", first["id"], runner())
    # An owner can still report never-started and return the reservation after grant revocation.
    report(rig.hub, first, runner(), kind="not_started", code=None)
    assert rig.hub.cluster("bob")[0]["reserved_cpus"] == 0


@pytest.fixture
def runtime(tmp_path):
    hub = Hub(Store(tmp_path / "hub.sqlite3"))
    hub.bootstrap()
    hub.create_node(NodeCreate(id="A", cpus=1, memory_mib=256))
    token = hub.create_user(
        UserCreate(
            id="tester", allowed_nodes=["A"], identity={"uid": os.getuid(), "gid": os.getgid()}
        )
    )["token"]
    env = local_env(
        hub,
        "tester",
        uid=os.getuid(),
        gid=os.getgid(),
        cpus=1,
        instance="runtime",
        workdir=str(tmp_path),
    )
    hub.local.approve(env)
    hub.provision_destination("tester", "fake-transport", "123")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(hub), log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    token_path = tmp_path / "user.token"
    token_path.write_text(token + "\n")
    token_path.chmod(0o600)
    config = tmp_path / "runner.json"
    save(
        config,
        {
            "hub_url": f"http://127.0.0.1:{listener.getsockname()[1]}",
            "token_file": str(token_path),
            "environment_id": env,
            "hostname": os.uname().nodename,
            "environment": {
                "instance_id": "runtime",
                "uid": os.getuid(),
                "gid": os.getgid(),
                "workdir": str(tmp_path),
            },
        },
    )
    records = []

    def start(code, *args):
        result = launch(config, [sys.executable, "-c", code, *args], 1, 64, workdir=tmp_path)
        records.append(result)
        return result

    try:
        yield hub, config, start
    finally:
        known = {r["record"] for r in records}
        for record in config.parent.glob("**/runs/*.json"):
            snapshot = json.loads(read_private(record))
            if str(record) not in known and snapshot.get("job_id"):
                records.append({"job_id": snapshot["job_id"], "record": str(record)})
        for r in records:
            hub.cancel("tester", r["job_id"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and any(
            json.loads(read_private(r["record"]))["phase"] not in ("DONE", "ERROR") for r in records
        ):
            time.sleep(0.05)
        for r in records:
            s = json.loads(read_private(r["record"]))
            if s["phase"] not in ("DONE", "ERROR") and s.get("supervisor_pid"):
                from cowork_hub.runner import children

                for pid in children(s["supervisor_pid"]) + [s["supervisor_pid"]]:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def wait_state(hub, result, desired, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = hub.get_job("tester", result["job_id"])
        if value["state"] == desired:
            return value
        time.sleep(0.03)
    pytest.fail(f"Job did not reach {desired}; state={value['state']}")


def test_real_process_queue_exit_logs_and_argument_preservation(runtime):
    hub, _, start = runtime
    first = start('import time; print("first", flush=True); time.sleep(2)')
    wait_state(hub, first, "RUNNING")
    second = start("import sys; print(repr(sys.argv[1])); sys.exit(7)", 'a b " c; $(false)')
    assert hub.get_job("tester", second["job_id"])["state"] == "QUEUED"
    done1 = wait_state(hub, first, "SUCCEEDED")
    done2 = wait_state(hub, second, "FAILED")
    assert done2["started_at"] >= done1["finished_at"]
    assert done2["exit_code"] == 7
    assert "a b" in Path(second["log_path"]).read_text()
    assert hub.cluster("tester")[0]["reserved_cpus"] == 0
    assert [x["kind"] for x in done2["notifications"]] == ["started", "finished"]


def test_real_process_descendants_hold_reservation_and_cancel_is_local(runtime):
    hub, _, start = runtime
    first = start(
        'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(2)"])'
    )
    wait_state(hub, first, "RUNNING")
    second = start('print("next")')
    assert hub.get_job("tester", second["job_id"])["state"] == "QUEUED"
    a = wait_state(hub, first, "SUCCEEDED")
    b = wait_state(hub, second, "SUCCEEDED")
    assert a["finished_at"] - a["started_at"] >= 1.8
    assert b["started_at"] >= a["finished_at"]
    third = start("import time; time.sleep(60)")
    wait_state(hub, third, "RUNNING")
    hub.cancel("tester", third["job_id"])
    assert hub.cluster("tester")[0]["reserved_cpus"] == 1
    wait_state(hub, third, "CANCELLED")
    assert hub.cluster("tester")[0]["reserved_cpus"] == 0


def test_losing_claim_attempt_cannot_release_the_winning_process(rig):
    from cowork_hub.models import Error

    env = local_env(rig.hub)
    rig.hub.local.approve(env)
    job = rig.hub.submit("alice", submission(env), runner=runner())
    rig.hub.local.claim("alice", job["id"], runner())
    loser = runner().model_copy(update={"claim_id": "claim-loser"})
    with pytest.raises(Error):
        rig.hub.local.claim("alice", job["id"], loser)
    with pytest.raises(Error, match="different claim"):
        report(rig.hub, job, loser, kind="not_started", code=None)
    assert rig.hub.get_job("alice", job["id"])["state"] == "DISPATCHING"
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 8
    report(rig.hub, job, runner())


def test_completion_retry_does_not_repeat_command(runtime, monkeypatch, tmp_path):
    from cowork_hub.models import Error

    hub, _, start = runtime
    original = hub.local.event
    failures = [0]

    def flaky(*args):
        if failures[0] < 2:
            failures[0] += 1
            # Simulate an acknowledgement loss: the hub commits before the transport fails.
            original(*args)
            raise Error("TEMPORARY", "simulated acknowledgement loss", 503)
        return original(*args)

    monkeypatch.setattr(hub.local, "event", flaky)
    marker = tmp_path / "executions.txt"
    job = start(
        'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.open("a").write("once\\n")', str(marker)
    )
    wait_state(hub, job, "SUCCEEDED")
    assert marker.read_text() == "once\n"
    assert failures[0] == 2
    assert len(hub.get_job("tester", job["job_id"])["notifications"]) == 2


def test_crashed_supervisor_never_reexecutes_uncertain_command(runtime, tmp_path):
    import subprocess

    hub, config, _ = runtime
    identity = runner("runtime", uid=os.getuid(), gid=os.getgid())
    environment_id = json.loads(read_private(config))["environment_id"]
    request = submission(environment_id, cpus=1)
    job = hub.submit("tester", request, runner=identity)
    hub.local.claim("tester", job["id"], identity)
    record = tmp_path / "uncertain.json"
    save(
        record,
        {
            "config": str(config),
            "runner_id": identity.runner_id,
            "claim_id": identity.claim_id,
            "phase": "RUNNING",
            "job_id": job["id"],
            "events": [],
        },
    )
    completed = subprocess.run(
        [sys.executable, "-m", "cowork_hub.runner", "_supervise", str(record)],
        capture_output=True,
        timeout=5,
    )
    assert completed.returncode == 125
    assert json.loads(read_private(record))["phase"] == "RECOVERY_REQUIRED"
    assert hub.get_job("tester", job["id"])["state"] == "DISPATCHING"
    assert hub.cluster("tester")[0]["reserved_cpus"] == 1


def test_foreground_cli_returns_original_exit_code(runtime):
    import subprocess

    hub, config, _ = runtime
    command = [
        sys.executable,
        "-m",
        "cowork_hub.runner",
        "--config",
        str(config),
        "--cpus",
        "1",
        "--mem",
        "64MiB",
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(7)",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    assert result.returncode == 7, result.stderr
    job_id = json.loads(result.stdout)["job_id"]
    assert hub.get_job("tester", job_id)["state"] == "FAILED"


def test_preflight_error_never_runs_unreserved_command(runtime, tmp_path):
    import subprocess

    hub, config, _ = runtime
    with hub.store.transaction() as db:
        db.execute("UPDATE notification_destinations SET enabled=0")
    marker = tmp_path / "must-not-run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cowork_hub.runner",
            "--config",
            str(config),
            "--cpus",
            "1",
            "--mem",
            "64MiB",
            "--",
            sys.executable,
            "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
            str(marker),
        ],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 125
    assert not marker.exists()
    assert hub.list_jobs("tester") == []


def test_hub_apply_helper_preserves_credentials_and_is_idempotent(tmp_path):
    import importlib.util
    import subprocess

    path = Path(__file__).resolve().parents[1] / "scripts/enable_local_runner.py"
    spec = importlib.util.spec_from_file_location("enable_local_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hub = Hub(Store(tmp_path / "hub.sqlite3"))
    admin = hub.bootstrap()
    (tmp_path / "admin.token").write_text(admin)
    (tmp_path / "admin.token").chmod(0o600)
    hub.create_node(NodeCreate(id="A", cpus=2, memory_mib=128))
    token = hub.create_user(UserCreate(id="tester", allowed_nodes=["A"]))["token"]
    env = dict(os.environ, HUB_DATA_DIR=str(tmp_path))
    backup = subprocess.run(
        [sys.executable, "-c", module.BACKUP], env=env, capture_output=True, timeout=5
    )
    assert backup.returncode == 0
    assert len(list(tmp_path.glob("hub-before-local-runner-*.sqlite3"))) == 1
    payload = {
        "user": "tester",
        "environment": LocalEnvironment(
            name="test",
            node_id="A",
            ssh_target="local",
            workdir=str(tmp_path),
            instance_id="test-instance",
            uid=1101,
            gid=1100,
            cpus=2,
            memory_mib=128,
        ).model_dump(),
    }
    ids = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", module.ENABLE],
            env=env,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        ids.append(json.loads(result.stdout)["environment_id"])
        assert token not in result.stdout and admin not in result.stdout
    assert ids[0] == ids[1]
    assert hub.authenticate(token)["id"] == "tester"
    assert hub.user_identity("tester") == {"configured": True, "uid": 1101, "gid": 1100}


def test_compose_override_references_existing_env_without_copying_webhook(tmp_path):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts/enable_local_runner.py"
    spec = importlib.util.spec_from_file_location("enable_local_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = {
        "user_id": "alice",
        "env_file": str(tmp_path / "existing.env"),
        "channel_id": "123",
        "guild_id": "999",
        "secret_ref": "existing",
    }
    settings = module.compose_settings(metadata, "alice")
    assert settings["services"]["hub"]["env_file"] == [metadata["env_file"]]
    assert "DISCORD_WEBHOOK_URL" not in json.dumps(settings)
    with pytest.raises(RuntimeError):
        module.compose_settings(metadata, "bob")


def test_mcp_submits_once_reconnects_cancels_and_runner_survives_mcp_exit(runtime, tmp_path):
    import asyncio
    from contextlib import asynccontextmanager

    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    hub, config_path, _ = runtime
    config = json.loads(read_private(config_path))
    gate = tmp_path / "release-mcp-job"
    marker = tmp_path / "only-once.txt"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "cowork_hub.mcp_server",
            "--hub-url",
            config["hub_url"],
            "--token-file",
            config["token_file"],
            "--runner-config",
            str(config_path),
        ],
    )

    def request(key, argv, mode="queue_if_unavailable"):
        return {
            "request_key": key,
            "mode": mode,
            "spec": {
                "name": key,
                "environment_ids": [config["environment_id"]],
                "argv": argv,
                "cpus": 1,
                "memory_mib": 64,
                "workdir": str(tmp_path),
            },
        }

    a = request("mcp-request-A", [sys.executable, "-c", "import time; time.sleep(20)"])
    b = request(
        "mcp-request-B",
        [
            sys.executable,
            "-c",
            "import pathlib,sys,time; gate=pathlib.Path(sys.argv[1]); "
            'exec("for _ in range(200):\\n if gate.exists(): break\\n time.sleep(.05)"); '
            'pathlib.Path(sys.argv[2]).open("a").write("once\\n")',
            str(gate),
            str(marker),
        ],
    )

    @asynccontextmanager
    async def connected():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def call(session, name, args):
        response = await session.call_tool(name, args)
        assert not response.isError, response
        return response.structuredContent

    async def wait(session, job_id, state):
        for _ in range(80):
            job = await call(session, "job_status", {"job_id": job_id})
            if job["state"] == state:
                return job
            await asyncio.sleep(0.05)
        pytest.fail(f"Job did not reach {state}")

    async def scenario():
        async with asyncio.timeout(25):
            async with connected() as session:
                first = await call(session, "submit_job", {"request": a})
                assert first["accepted"] and first["agent_action"] == "finish_turn"
                await wait(session, first["job_id"], "RUNNING")
                plan = await call(session, "plan_job", {"primary": b["spec"]})
                assert (
                    plan["choices"][0]["kind"] == "wait" and plan["alternatives_need_preparation"]
                )
                async with asyncio.timeout(5):
                    second = await call(session, "submit_job", {"request": b})
                assert second["accepted"] and second["agent_action"] == "finish_turn"
                # Acceptance is sufficient to finish the turn while the job still cannot start.
                assert hub.get_job("tester", second["job_id"])["state"] == "QUEUED"
                again = await call(session, "submit_job", {"request": b})
                assert again["job_id"] == second["job_id"] and again["reused"]
                assert again["agent_action"] == "finish_turn"
                conflict = {**b, "spec": {**b["spec"], "argv": ["true"]}}
                denied = await session.call_tool("submit_job", {"request": conflict})
                assert denied.isError and "IDEMPOTENCY_CONFLICT" in denied.model_dump_json()
                immediate = request("mcp-no-silent-queue", ["true"], "start_if_available")
                denied = await session.call_tool("submit_job", {"request": immediate})
                assert denied.isError and "CAPACITY_UNAVAILABLE" in denied.model_dump_json()
                cancelled = await call(session, "cancel_job", {"job_id": first["job_id"]})
                assert cancelled["cancel_requested"]
                await wait(session, first["job_id"], "CANCELLED")
                await wait(session, second["job_id"], "RUNNING")
            # The first MCP process is now gone, while the independent runner still waits for its file.
            assert hub.get_job("tester", second["job_id"])["state"] == "RUNNING"
            async with connected() as session:
                repeated = await call(session, "submit_job", {"request": b})
                assert repeated["job_id"] == second["job_id"] and repeated["reused"]
            gate.touch()
            deadline = time.monotonic() + 5
            while (
                time.monotonic() < deadline
                and hub.get_job("tester", second["job_id"])["state"] != "SUCCEEDED"
            ):
                await asyncio.sleep(0.05)
            assert hub.get_job("tester", second["job_id"])["state"] == "SUCCEEDED"
            assert marker.read_text() == "once\n"
            assert len(hub.list_jobs("tester")) == 2
            assert hub.cluster("tester")[0]["reserved_cpus"] == 0

    asyncio.run(scenario())
