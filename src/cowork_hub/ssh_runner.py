"""Launch the existing pull runner over user SSH; only the main container needs MCP.

This first transport uses the lab's shared project, Python runtime and private token path.
The remote process receives structured JSON on stdin, never a shell-interpolated job command.
"""

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import Field, field_validator

from .models import Identifier, Input, JobSubmit, LocalEnvironment
from .runner import Client, RunnerError, load_config, read_private, save, submit_local

PREFIX = "COWORK_SSH_RESULT "
MAX_INPUT = 262144


class SSHTarget(Input):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
    user: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    port: int = Field(default=22, ge=1, le=65535)


class SSHRegistration(SSHTarget):
    node_id: Identifier
    workdir: str = Field(min_length=1, max_length=1024)

    @field_validator("workdir")
    @classmethod
    def absolute_workdir(cls, value):
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("workdir must be an absolute container path")
        return value


def checked_config(path, hub_url, token_file):
    config = load_config(Path(path).resolve())
    if (
        config["hub_url"].rstrip("/") != hub_url.rstrip("/")
        or Path(config["token_file"]).resolve() != Path(token_file).expanduser().resolve()
    ):
        raise RunnerError("RUNNER_CONFIG_MISMATCH")
    return config


def ssh_call(target, python, payload):
    target = SSHTarget.model_validate(target)
    if not Path(python).is_absolute() or "\x00" in python:
        raise RunnerError("SSH_RUNTIME_INVALID")
    command = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        "-p",
        str(target.port),
        "-l",
        target.user,
        target.host,
        shlex.join([python, "-m", "cowork_hub.ssh_runner"]),
    ]
    try:
        response = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=35,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # An SSH failure can happen after remote acceptance. The caller must keep the retry key.
        raise RunnerError("SSH_RESULT_UNCERTAIN") from None
    if response.returncode != 0 or len(response.stdout) > MAX_INPUT:
        raise RunnerError("SSH_RESULT_UNCERTAIN")
    lines = [
        line[len(PREFIX) :] for line in response.stdout.splitlines() if line.startswith(PREFIX)
    ]
    try:
        if len(lines) != 1:
            raise ValueError
        result = json.loads(lines[0])
        if not isinstance(result, dict):
            raise ValueError
    except ValueError:
        raise RunnerError("SSH_RESULT_UNCERTAIN") from None
    if "error" in result:
        code = result["error"]
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,64}", code):
            code = "SSH_RUNNER_ERROR"
        raise RunnerError(code)
    return result


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if path.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RunnerError("SSH_CONFIG_PERMISSIONS")


def register_ssh(config_path, registration: SSHRegistration, *, hub_url, token_file):
    config_path = Path(config_path).resolve()
    config = checked_config(config_path, hub_url, token_file)
    # Check the grant before attempting an SSH connection or creating remote state.
    client = Client(config)
    try:
        nodes = client.request("GET", "/v1/cluster")
    finally:
        client.close()
    if registration.node_id not in {node["id"] for node in nodes}:
        raise RunnerError("FORBIDDEN_NODE")
    key = hashlib.sha256(json.dumps(registration.model_dump(), sort_keys=True).encode()).hexdigest()
    directory = config_path.parent / "ssh-runners" / key
    private_directory(directory.parent)
    private_directory(directory)
    remote_config = directory / "runner.json"
    target = SSHTarget(**registration.model_dump(include={"host", "user", "port"}))
    result = ssh_call(
        target.model_dump(),
        sys.executable,
        {
            "action": "prepare",
            "registration": registration.model_dump(),
            "config_path": str(remote_config),
            "hub_url": hub_url,
            "token_file": str(Path(token_file).expanduser().resolve()),
        },
    )
    env_id = result.get("environment_id")
    if not isinstance(env_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", env_id):
        raise RunnerError("SSH_RESULT_UNCERTAIN")
    # Shared storage also lets us verify that the remote configuration was actually written.
    remote = json.loads(read_private(remote_config))
    if remote.get("environment_id") != env_id:
        raise RunnerError("SSH_SHARED_PATH_REQUIRED")
    fd = os.open(
        config_path.with_suffix(".ssh.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        updated = checked_config(config_path, hub_url, token_file)
        routes = updated.setdefault("ssh_runners", {})
        route = {
            "target": target.model_dump(),
            "python": sys.executable,
            "config_path": str(remote_config),
        }
        if env_id in routes and any(
            routes[env_id].get(key) != route[key] for key in ("target", "config_path")
        ):
            raise RunnerError("SSH_ROUTE_CONFLICT")
        routes[env_id] = route
        save(config_path, updated)
    return {
        **result,
        "config_path": str(remote_config),
        "ssh_connected": True,
        "submission_transport": "ssh",
        "requires_approval": not result["approved"],
    }


def submit(config_path, request: JobSubmit, *, hub_url, token_file):
    config = checked_config(config_path, hub_url, token_file)
    if request.spec.environment_ids == [config.get("environment_id")]:
        return submit_local(config_path, request, hub_url=hub_url, token_file=token_file)
    if len(request.spec.environment_ids) != 1:
        raise RunnerError("SELECT_ONE_ENVIRONMENT")
    route = config.get("ssh_runners", {}).get(request.spec.environment_ids[0])
    if route is None:
        raise RunnerError("SSH_RUNNER_NOT_CONFIGURED")
    result = ssh_call(
        route["target"],
        route["python"],
        {
            "action": "submit",
            "config_path": route["config_path"],
            "hub_url": hub_url,
            "token_file": str(Path(token_file).expanduser().resolve()),
            "request": request.model_dump(),
        },
    )
    if not isinstance(result.get("job_id"), str) or not result.get("record"):
        raise RunnerError("SSH_RESULT_UNCERTAIN")
    return {**result, "submission_transport": "ssh", "ssh_target": route["target"]}


def prepare_remote(payload, *, local=False):
    registration = SSHRegistration.model_validate(payload["registration"])
    path = Path(payload["config_path"])
    private_directory(path.parent)
    fd = os.open(path.with_suffix(".prepare.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        base = {"hub_url": payload["hub_url"], "token_file": payload["token_file"]}
        client = Client(base)
        try:
            identity = client.request("GET", "/v1/identity")
            if not identity.get("configured"):
                raise RunnerError("USER_IDENTITY_REQUIRED")
            if (identity["uid"], identity["gid"]) != (os.getuid(), os.getgid()):
                raise RunnerError("IDENTITY_MISMATCH")
            nodes = client.request("GET", "/v1/cluster")
            node = next((n for n in nodes if n["id"] == registration.node_id), None)
            if node is None:
                raise RunnerError("FORBIDDEN_NODE")
            if not Path(registration.workdir).is_dir():
                raise RunnerError("SSH_WORKDIR_MISSING")
            if path.exists():
                config = checked_config(path, payload["hub_url"], payload["token_file"])
                if config.get("ssh_registration") != registration.model_dump():
                    raise RunnerError("SSH_ROUTE_CONFLICT")
            else:
                gpu_ids = []
                if node["gpus"] and shutil.which("nvidia-smi"):
                    try:
                        probe = subprocess.run(
                            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
                            capture_output=True,
                            text=True,
                            timeout=8,
                            check=True,
                        )
                    except (OSError, subprocess.SubprocessError):
                        raise RunnerError("SSH_GPU_PROBE_FAILED") from None
                    gpu_ids = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
                    if not set(gpu_ids) <= {g["id"] for g in node["gpus"]}:
                        raise RunnerError("SSH_GPU_NODE_MISMATCH")
                memory = next(
                    int(line.split()[1]) // 1024
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemTotal:")
                )
                env = LocalEnvironment(
                    name=f"{registration.node_id}-{os.uname().nodename}",
                    node_id=registration.node_id,
                    ssh_target=(
                        "local"
                        if local
                        else f"ssh -p {registration.port} {registration.user}@{registration.host}"
                    ),
                    workdir=registration.workdir,
                    instance_id=uuid.uuid4().hex,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    cpus=min(os.cpu_count() or 1, node["cpus"]),
                    memory_mib=min(memory, node["memory_mib"]),
                    gpu_ids=gpu_ids,
                )
                config = {
                    **base,
                    "hostname": os.uname().nodename,
                    "environment": env.model_dump(),
                    "ssh_registration": registration.model_dump(),
                }
                # Persist the instance ID before registration so a lost reply is safe to retry.
                save(path, config)
            registered = client.request("POST", "/v1/local/environments", config["environment"])
            config["environment_id"] = registered["id"]
            save(path, config)
        finally:
            client.close()
    return {
        "environment_id": registered["id"],
        "approved": registered["approved"],
        "environment": config["environment"],
        "hostname": config["hostname"],
    }


def main():
    os.umask(0o077)
    try:
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise RunnerError("REQUEST_TOO_LARGE")
        payload = json.loads(raw)
        if payload["action"] == "prepare":
            result = prepare_remote(payload)
        elif payload["action"] == "submit":
            result = submit_local(
                payload["config_path"],
                JobSubmit.model_validate(payload["request"]),
                hub_url=payload["hub_url"],
                token_file=payload["token_file"],
            )
        else:
            raise RunnerError("INVALID_REQUEST")
    except RunnerError as exc:
        code = str(exc).split(":", 1)[0].split(" ", 1)[0]
        result = {"error": code if re.fullmatch(r"[A-Z_]{1,64}", code) else "SSH_RUNNER_ERROR"}
    except Exception:
        result = {"error": "SSH_RUNNER_ERROR"}
    print(PREFIX + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
