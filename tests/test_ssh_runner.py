import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_local_runner import runtime as runtime
from test_local_runner import wait_state

from cowork_hub.models import JobSubmit, NodeCreate
from cowork_hub.runner import RunnerError, read_private
from cowork_hub.ssh_runner import (
    PREFIX,
    SSHRegistration,
    SSHTarget,
    prepare_remote,
    register_ssh,
    ssh_call,
    submit,
)


@pytest.fixture
def ssh_transport(monkeypatch):
    """Emulate only SSH transport; the target Python process and pull runner are real."""
    real_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        if command[0] != "ssh":
            return real_run(command, **kwargs)
        calls.append((command, json.loads(kwargs["input"])))
        return real_run(shlex.split(command[-1]), **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def registration(directory, node="A"):
    return SSHRegistration(
        host="container.example", user="tester", port=11010, node_id=node, workdir=str(directory)
    )


def connect(config_path, target):
    config = json.loads(read_private(config_path))
    return register_ssh(
        config_path, target, hub_url=config["hub_url"], token_file=config["token_file"]
    )


def send(config_path, request):
    config = json.loads(read_private(config_path))
    return submit(config_path, request, hub_url=config["hub_url"], token_file=config["token_file"])


@pytest.mark.parametrize(
    "fields",
    [
        {"host": "-oProxyCommand=bad", "user": "tester"},
        {"host": "host; bad", "user": "tester"},
        {"host": "host", "user": "tester$(bad)"},
        {"host": "host", "user": "tester", "port": 0},
    ],
)
def test_ssh_options_cannot_be_supplied_as_host_or_user(fields):
    with pytest.raises(ValidationError):
        SSHTarget(**fields)


def test_ssh_command_uses_fixed_module_and_json_stdin(monkeypatch):
    payload = {"request": {"argv": ["echo", "$(false); 'quoted'"], "request_key": "stable-key"}}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, "login banner\n" + PREFIX + '{"ok":true}\n', ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert ssh_call(
        {"host": "host", "user": "tester", "port": 11010}, "/shared path/python", payload
    ) == {"ok": True}
    command, options = calls[0]
    assert shlex.split(command[-1]) == ["/shared path/python", "-m", "cowork_hub.ssh_runner"]
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    assert json.loads(options["input"]) == payload
    assert "$(false)" not in " ".join(command)
    assert not options.get("shell")


def test_ssh_failure_does_not_echo_remote_stderr(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 255, "secret", "private diagnostic"),
    )
    with pytest.raises(RunnerError, match="^SSH_RESULT_UNCERTAIN$"):
        ssh_call({"host": "host", "user": "tester"}, sys.executable, {})


def test_register_checks_grant_before_ssh(runtime, ssh_transport, tmp_path):
    _, config, _ = runtime
    with pytest.raises(RunnerError, match="FORBIDDEN_NODE"):
        connect(config, registration(tmp_path, "not-allowed"))
    assert ssh_transport == []


def test_remote_registration_is_idempotent_and_ready_without_approval(
    runtime, ssh_transport, tmp_path
):
    hub, config, _ = runtime
    target = registration(tmp_path)
    first = connect(config, target)
    second = connect(config, target)
    assert first["environment_id"] == second["environment_id"]
    assert not first["requires_approval"] and second["approved"]
    assert len(hub.list_environments("tester")) == 2
    request = JobSubmit(
        request_key="automatic-registration",
        spec={
            "name": "automatic",
            "environment_ids": [first["environment_id"]],
            "argv": ["false"],
            "cpus": 1,
            "memory_mib": 64,
            "workdir": str(tmp_path),
        },
    )
    submitted = send(config, request)
    assert submitted["job_id"]
    wait_state(hub, submitted, "FAILED")
    assert len(hub.list_jobs("tester")) == 1
    confirmed = connect(config, target)
    assert confirmed["approved"] and not confirmed["requires_approval"]
    assert len(hub.list_environments("tester")) == 2


def test_mcp_remote_runner_survives_ssh_exit_retries_lost_reply_and_cancels(
    runtime, ssh_transport, tmp_path, monkeypatch
):
    pytest.importorskip("mcp")
    from cowork_hub.mcp_server import HubClient, create_mcp

    hub, config_path, start = runtime
    config = json.loads(read_private(config_path))
    server = create_mcp(HubClient(config["hub_url"], Path(config["token_file"])), config_path)

    async def tool(name, args):
        _, structured = await server.call_tool(name, args)
        return structured

    registered = asyncio.run(
        tool("register_ssh_environment", {"target": registration(tmp_path).model_dump()})
    )
    blocker = start("import time; time.sleep(20)")
    wait_state(hub, blocker, "RUNNING")
    marker = tmp_path / "once.txt"
    job = JobSubmit(
        request_key="ssh-lost-response",
        spec={
            "name": "remote",
            "environment_ids": [registered["environment_id"]],
            "argv": [
                sys.executable,
                "-c",
                'import pathlib,sys,time; pathlib.Path(sys.argv[1]).open("a").write("once\\n"); time.sleep(20)',
                str(marker),
            ],
            "cpus": 1,
            "memory_mib": 64,
            "workdir": str(tmp_path),
        },
    )
    emulated_run = subprocess.run
    lost = []

    def lose_first_response(command, **kwargs):
        response = emulated_run(command, **kwargs)
        if command[0] == "ssh" and json.loads(kwargs["input"])["action"] == "submit" and not lost:
            lost.append(True)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return response

    monkeypatch.setattr(subprocess, "run", lose_first_response)
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="SSH_RESULT_UNCERTAIN"):
        asyncio.run(tool("submit_job", {"request": job.model_dump()}))
    accepted = asyncio.run(tool("submit_job", {"request": job.model_dump()}))
    assert accepted["reused"] and accepted["submission_transport"] == "ssh"
    assert accepted["accepted"] and accepted["agent_action"] == "finish_turn"
    wait_state(hub, accepted, "QUEUED")
    assert not marker.exists()
    asyncio.run(tool("cancel_job", {"job_id": blocker["job_id"]}))
    wait_state(hub, blocker, "CANCELLED")
    wait_state(hub, accepted, "RUNNING")
    # Every SSH-emulating subprocess has exited; the detached target runner is still running.
    repeated = asyncio.run(tool("submit_job", {"request": job.model_dump()}))
    assert repeated["job_id"] == accepted["job_id"] and repeated["reused"]
    assert marker.read_text() == "once\n"
    asyncio.run(tool("cancel_job", {"job_id": accepted["job_id"]}))
    wait_state(hub, accepted, "CANCELLED")
    assert len(hub.list_jobs("tester")) == 2
    assert hub.cluster("tester")[0]["reserved_cpus"] == 0


def test_unknown_environment_never_attempts_ssh(runtime, ssh_transport, tmp_path):
    _, config, _ = runtime
    request = JobSubmit(
        request_key="unregistered-target",
        spec={
            "name": "test",
            "environment_ids": ["unregistered"],
            "argv": ["false"],
            "cpus": 1,
            "memory_mib": 64,
            "workdir": str(tmp_path),
        },
    )
    with pytest.raises(RunnerError, match="SSH_RUNNER_NOT_CONFIGURED"):
        send(config, request)
    assert not ssh_transport


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("gpu_ids", [[], ["GPU-first", "GPU-second"]])
def test_registration_uses_node_gpus_without_probing(
    runtime, tmp_path, monkeypatch, local, gpu_ids
):
    hub, config_path, _ = runtime
    hub.create_node(
        NodeCreate(
            id="B",
            cpus=1,
            memory_mib=256,
            gpus=[{"id": gpu_id, "model": "test", "memory_mib": 1024} for gpu_id in gpu_ids],
        )
    )
    hub.set_grants("tester", ["A", "B"])
    config = json.loads(read_private(config_path))
    monkeypatch.setattr(
        "shutil.which", lambda name: pytest.fail("Registration looked for a GPU tool")
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Registration launched a GPU probe"),
    )
    payload = {
        "registration": registration(tmp_path, "B").model_dump(),
        "config_path": str(tmp_path / "gpu/runner.json"),
        "hub_url": config["hub_url"],
        "token_file": config["token_file"],
    }
    result = prepare_remote(payload, local=local)
    assert result["environment"]["gpu_ids"] == gpu_ids
    assert result["approved"]
    assert (result["environment"]["ssh_target"] == "local") is local
    assert prepare_remote(payload, local=local)["environment_id"] == result["environment_id"]
    saved = json.loads(read_private(payload["config_path"]))
    assert saved["environment"]["gpu_ids"] == gpu_ids
    environment = next(
        e for e in hub.list_environments("tester") if e["id"] == result["environment_id"]
    )
    assert environment["gpu_ids"] == gpu_ids
    assert environment["status"] == "READY"
    assert len(hub.list_environments("tester")) == 2


def test_plan_marks_ready_remote_route_as_submittable_from_main(runtime, ssh_transport, tmp_path):
    pytest.importorskip("mcp")
    from cowork_hub.mcp_server import HubClient, create_mcp

    hub, config_path, start = runtime
    hub.create_node(NodeCreate(id="B", cpus=1, memory_mib=256))
    hub.set_grants("tester", ["A", "B"])
    registered = connect(config_path, registration(tmp_path, "B"))
    blocker = start("import time; time.sleep(20)")
    wait_state(hub, blocker, "RUNNING")
    config = json.loads(read_private(config_path))
    server = create_mcp(HubClient(config["hub_url"], Path(config["token_file"])), config_path)
    primary = {
        "name": "busy",
        "environment_ids": [config["environment_id"]],
        "argv": ["true"],
        "cpus": 1,
        "memory_mib": 64,
        "workdir": str(tmp_path),
    }
    _, result = asyncio.run(server.call_tool("plan_job", {"primary": primary}))
    choice = next(c for c in result["choices"] if c["kind"] == "use_other_environment")
    assert choice["environment_id"] == registered["environment_id"]
    assert choice["submission_transport"] == "ssh" and not choice["requires_target_runner"]
    assert len(hub.list_jobs("tester")) == 1
