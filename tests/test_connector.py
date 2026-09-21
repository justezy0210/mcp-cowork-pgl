import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_local_runner import runtime as runtime
from test_ssh_runner import ssh_transport as ssh_transport

from cowork_hub.connector import (
    configure,
    process_identity,
    run,
    start,
    startup,
    status,
    step,
    stop,
)
from cowork_hub.connector_models import RegistrationCreate
from cowork_hub.runner import Client, Retryable, RunnerError, read_private, save


def enqueue(hub, connector_id, workdir):
    return hub.management.request_registration(
        "tester",
        RegistrationCreate(
            request_key="connector-target-1",
            connector_id=connector_id,
            target={
                "host": "target.example",
                "user": "tester",
                "port": 11010,
                "node_id": "A",
                "workdir": str(workdir),
            },
        ),
    )


def test_connector_reuses_registration_after_lost_ack_and_restart(
    runtime, ssh_transport, tmp_path, monkeypatch
):
    hub, runner, _ = runtime
    path = tmp_path / "connector.json"
    first = configure(path, runner_config=runner)
    assert configure(path, runner_config=runner)["connector_id"] == first["connector_id"]
    assert path.stat().st_mode & 0o077 == 0
    queued = enqueue(hub, first["connector_id"], tmp_path)
    real_request = Client.request
    lost = []

    def lose_ack(self, method, endpoint, body=None):
        response = real_request(self, method, endpoint, body)
        if endpoint.endswith("/result") and not lost:
            lost.append(True)
            raise Retryable("HUB_UNREACHABLE")
        return response

    monkeypatch.setattr(Client, "request", lose_ack)
    with pytest.raises(Retryable):
        step(path)
    assert path.with_suffix(".result.json").exists()
    requests = hub.management.list_requests("tester")
    assert requests[0]["id"] == queued["id"] and requests[0]["state"] == "REGISTERED"
    before = len(ssh_transport)
    assert step(path)["result_delivered"]
    assert len(ssh_transport) == before
    assert not path.with_suffix(".result.json").exists()
    assert step(path)["idle"]
    env_id = requests[0]["environment_id"]
    assert env_id in json.loads(read_private(runner))["ssh_runners"]
    assert (
        next(e for e in hub.list_environments("tester") if e["id"] == env_id)["status"]
        == "PENDING_APPROVAL"
    )
    assert hub.list_jobs("tester") == []


def test_new_main_configuration_with_token_registers_without_approving(runtime, tmp_path):
    hub, runner, _ = runtime
    previous = json.loads(read_private(runner))
    path = tmp_path / "fresh" / "connector.json"
    configured = configure(
        path,
        hub_url=previous["hub_url"],
        token_file=previous["token_file"],
        node="A",
        workdir=str(tmp_path),
    )
    repeated = configure(
        path,
        hub_url=previous["hub_url"],
        token_file=previous["token_file"],
        node="A",
        workdir=str(tmp_path),
    )
    assert repeated["connector_id"] == configured["connector_id"]
    env = next(
        e for e in hub.list_environments("tester") if e["id"] == configured["environment_id"]
    )
    assert env["status"] == "PENDING_APPROVAL" and env["ssh_target"] == "local"
    assert path.parent.stat().st_mode & 0o077 == 0
    assert run(path, once=True)["idle"]


def test_daemon_survives_launcher_exit_and_registers_without_mcp(runtime, tmp_path, monkeypatch):
    hub, runner, _ = runtime
    path = tmp_path / "connector.json"
    configured = configure(path, runner_config=runner)
    queued = enqueue(hub, configured["connector_id"], tmp_path)
    # Only transport is emulated. The connector and target registration are actual processes.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!"
        + sys.executable
        + "\nimport os,shlex,sys\nargs=shlex.split(sys.argv[-1])\nos.execv(args[0],args)\n"
    )
    ssh.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cowork_hub.connector", "start", "--config", str(path)],
            capture_output=True,
            text=True,
            timeout=12,
            check=True,
        )
        started = json.loads(result.stdout)
        assert started["running"] and status(path)["running"]
        assert start(path)["pid"] == started["pid"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            item = hub.management.list_requests("tester")[0]
            if item["state"] == "REGISTERED":
                break
            time.sleep(0.05)
        assert item["state"] == "REGISTERED" and item["id"] == queued["id"]
        assert hub.management.list_connectors("tester")[0]["online"]
        assert hub.list_jobs("tester") == []
        entry = startup(path)
        assert not entry["autostart_configured"]
        assert str(path) in Path(entry["startup_script"]).read_text()
        assert startup(path) == entry
    finally:
        stop(path)
        deadline = time.monotonic() + 5
        while status(path)["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not status(path)["running"]


def test_stale_pid_and_different_container_are_never_stopped(runtime, tmp_path, monkeypatch):
    _, runner, _ = runtime
    path = tmp_path / "connector.json"
    configure(path, runner_config=runner)
    fake = {**process_identity(os.getpid()), "start_ticks": "different"}
    save(path.with_suffix(".process.json"), fake)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Stale PID was signalled"))
    assert not stop(path)["stop_requested"]
    config = json.loads(read_private(path))
    config["hostname"] = "another-container"
    save(path, config)
    with pytest.raises(RunnerError, match="CONTAINER_MISMATCH"):
        status(path)


def test_runtime_refresh_preserves_registered_target(runtime, ssh_transport, tmp_path):
    from cowork_hub.ssh_runner import SSHRegistration, register_ssh

    _, runner, _ = runtime
    config = json.loads(read_private(runner))
    target = SSHRegistration(
        host="target.example", user="tester", node_id="A", workdir=str(tmp_path)
    )
    first = register_ssh(runner, target, hub_url=config["hub_url"], token_file=config["token_file"])
    config = json.loads(read_private(runner))
    config["ssh_runners"][first["environment_id"]]["python"] = "/previous/runtime/python"
    save(runner, config)
    second = register_ssh(
        runner, target, hub_url=config["hub_url"], token_file=config["token_file"]
    )
    assert first["environment_id"] == second["environment_id"]
    assert (
        json.loads(read_private(runner))["ssh_runners"][first["environment_id"]]["python"]
        == sys.executable
    )


def test_release_excludes_local_credentials_and_installer_preserves_existing_directory(tmp_path):
    import tarfile

    root = Path(__file__).resolve().parents[1]

    def module(name):
        spec = importlib.util.spec_from_file_location(name, root / "scripts" / (name + ".py"))
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded

    build = module("build_client_release").build
    archive = build(root, tmp_path / "release")
    with tarfile.open(archive) as package:
        names = package.getnames()
        assert any(n.endswith("scripts/install_connector.py") for n in names)
        assert all(
            "/.local/" not in n and not n.endswith(".token") and "/.env" not in n for n in names
        )
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "preserve.txt"
    marker.write_text("keep")
    with pytest.raises(RuntimeError):
        module("install_connector").install(existing, root)
    assert marker.read_text() == "keep"
