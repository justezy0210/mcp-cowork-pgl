"""A user's main-container service: process SSH registrations without an LLM."""

import argparse
import fcntl
import json
import os
import pwd
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .connector_models import RegistrationResult
from .runner import Client, RunnerError, load_config, read_private, save
from .ssh_runner import SSHRegistration, prepare_remote, private_directory, register_ssh


def read_config(path):
    config = json.loads(read_private(path))
    if config["hostname"] != os.uname().nodename:
        raise RunnerError("CONNECTOR_CONTAINER_MISMATCH")
    load_config(config["runner_config"])
    return config


def require_capability(client):
    if client.request("GET", "/v1/capabilities").get("connector") != 1:
        raise RunnerError("HUB_UPGRADE_REQUIRED: deploy connector API version 1 first")


def configure(
    path, *, runner_config=None, hub_url=None, token_file=None, node=None, workdir=None, name=None
):
    path = Path(path).expanduser().absolute()
    private_directory(path.parent)
    if runner_config:
        runner_config = Path(runner_config).expanduser().absolute()
        runner = load_config(runner_config)
    else:
        if not all((hub_url, token_file, node, workdir)):
            raise RunnerError(
                "CONFIGURATION_REQUIRED: supply runner config or hub/token/node/workdir"
            )
        runner_config = path.parent / "runner.json"
        base = {"hub_url": hub_url, "token_file": str(Path(token_file).expanduser().absolute())}
        client = Client(base)
        try:
            require_capability(client)
        finally:
            client.close()
        prepare_remote(
            {
                **base,
                "config_path": str(runner_config),
                "registration": {
                    "host": os.uname().nodename,
                    "user": pwd.getpwuid(os.getuid()).pw_name,
                    "port": 22,
                    "node_id": node,
                    "workdir": workdir,
                },
            },
            local=True,
        )
        runner = load_config(runner_config)
    if not runner.get("environment_id"):
        raise RunnerError("MAIN_ENVIRONMENT_REQUIRED: register the main runner first")
    client = Client(runner)
    try:
        require_capability(client)
        if path.exists():
            config = read_config(path)
            if config["runner_config"] != str(runner_config):
                raise RunnerError("CONNECTOR_CONFIG_CONFLICT")
        else:
            config = {
                "instance_id": uuid.uuid4().hex,
                "runner_config": str(runner_config),
                "hostname": os.uname().nodename,
                "name": name or os.uname().nodename,
            }
            # Keep the instance ID across a lost registration response.
            save(path, config)
        registered = client.request(
            "POST",
            "/v1/connectors",
            {
                "instance_id": config["instance_id"],
                "name": config["name"],
                "environment_id": runner["environment_id"],
            },
        )
        config["connector_id"] = registered["id"]
        save(path, config)
    finally:
        client.close()
    return {
        "connector_id": config["connector_id"],
        "config": str(path),
        "environment_id": runner["environment_id"],
    }


def step(path):
    """One bounded request; persist the outcome before delivery, including across restarts."""
    path = Path(path)
    config = read_config(path)
    runner = load_config(config["runner_config"])
    client = Client(runner)
    journal = path.with_suffix(".result.json")
    prefix = "/v1/connectors/" + config["connector_id"]
    try:
        if journal.exists():
            pending = json.loads(read_private(journal))
            stale = False
            try:
                client.request(
                    "POST",
                    prefix + "/requests/" + pending["request_id"] + "/result",
                    pending["result"],
                )
            except RunnerError as exc:
                if not str(exc).startswith("STALE_CLAIM"):
                    raise
                stale = True
            journal.unlink()
            return {"result_delivered": not stale, "stale_claim": stale}
        request = client.request("POST", prefix + "/poll", {"instance_id": config["instance_id"]})[
            "request"
        ]
        if request is None:
            return {"idle": True}
        outcome = {"instance_id": config["instance_id"], "claim_id": request["claim_id"]}
        try:
            result = register_ssh(
                config["runner_config"],
                SSHRegistration.model_validate(request["target"]),
                hub_url=runner["hub_url"],
                token_file=runner["token_file"],
            )
            outcome["environment_id"] = result["environment_id"]
        except (RunnerError, OSError, ValueError, KeyError, TypeError) as exc:
            code = (
                str(exc).split(":", 1)[0].split(" ", 1)[0]
                if isinstance(exc, RunnerError)
                else "SSH_RUNNER_ERROR"
            )
            try:
                RegistrationResult(**outcome, error_code=code)
            except ValidationError:
                code = "SSH_RUNNER_ERROR"
            outcome["error_code"] = code
        save(journal, {"request_id": request["id"], "result": outcome})
        client.request("POST", prefix + "/requests/" + request["id"] + "/result", outcome)
        journal.unlink()
        return {"request_id": request["id"], "registered": "environment_id" in outcome}
    finally:
        client.close()


def process_identity(pid):
    # Include boot identity and start time so a recycled PID is never stopped.
    data = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if data[0] == "Z":
        raise ProcessLookupError
    return {
        "pid": pid,
        "start_ticks": data[19],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def status(path):
    path = Path(path)
    read_config(path)
    try:
        recorded = json.loads(read_private(path.with_suffix(".process.json")))
        if recorded == process_identity(recorded["pid"]):
            return {"running": True, "pid": recorded["pid"]}
    except (OSError, ValueError, KeyError):
        pass
    return {"running": False}


def run(path, *, once=False):
    path = Path(path).absolute()
    config = read_config(path)
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError("CONNECTOR_ALREADY_RUNNING") from None
        if once:
            client = Client(load_config(config["runner_config"]))
            try:
                require_capability(client)
            finally:
                client.close()
            return step(path)
        save(path.with_suffix(".process.json"), process_identity(os.getpid()))
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        delay = 5
        verified = False
        while not stop.is_set():
            try:
                if not verified:
                    client = Client(load_config(config["runner_config"]))
                    try:
                        require_capability(client)
                    finally:
                        client.close()
                    verified = True
                step(path)
                delay = 5
            except (RunnerError, OSError, ValueError, KeyError):
                print(
                    "CONNECTOR_RETRY: check hub access, credentials and registration status.",
                    file=sys.stderr,
                    flush=True,
                )
                delay = min(delay * 2, 30)
            stop.wait(delay)
    return {"stopped": True}


def start(path):
    path = Path(path).absolute()
    read_config(path)
    fd = os.open(path.with_suffix(".start.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = status(path)
        if current["running"]:
            return current
        log_path = path.with_suffix(".log")
        log_fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_fd, "a") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "cowork_hub.connector", "run", "--config", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RunnerError("CONNECTOR_START_FAILED: inspect the connector log")
            current = status(path)
            if current["running"]:
                return {**current, "log_path": str(log_path)}
            time.sleep(0.05)
        raise RunnerError("CONNECTOR_START_UNCONFIRMED: inspect status before starting again")


def stop(path):
    current = status(path)
    if current["running"]:
        os.kill(current["pid"], signal.SIGTERM)
    return {"stop_requested": current["running"]}


def startup(path):
    path = Path(path).absolute()
    read_config(path)
    script = path.with_suffix(".startup.sh")
    command = shlex.join(
        [sys.executable, "-m", "cowork_hub.connector", "run", "--config", str(path)]
    )
    contents = (
        "#!/bin/sh\n# Run under the main container's existing process supervisor.\nexec "
        + command
        + "\n"
    )
    if script.exists():
        if read_private(script) != contents:
            raise RunnerError("STARTUP_FILE_EXISTS")
    else:
        fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
    return {"startup_script": str(script), "autostart_configured": False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "configure", help="Reuse a main runner, or register one with hub/token/node/workdir"
    )
    setup.add_argument("--config", type=Path, required=True)
    setup.add_argument("--runner-config", type=Path)
    setup.add_argument("--hub-url")
    setup.add_argument("--token-file", type=Path)
    setup.add_argument("--node")
    setup.add_argument("--workdir")
    setup.add_argument("--name")
    for name in ("run", "start", "stop", "status", "startup"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "run":
            command.add_argument("--once", action="store_true")
    args = vars(parser.parse_args())
    command, path = args.pop("command"), args.pop("config")
    try:
        result = {
            "configure": configure,
            "run": run,
            "start": start,
            "stop": stop,
            "status": status,
            "startup": startup,
        }[command](path, **args)
        print(json.dumps(result), flush=True)
    except (RunnerError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never emit config, HTTP response text, SSH stderr or credential contents.
        hints = {
            "HUB_UPGRADE_REQUIRED": "Deploy hub connector API version 1 first.",
            "CONNECTOR_ALREADY_RUNNING": "This connector is already running.",
            "CONNECTOR_CONTAINER_MISMATCH": "Use the configuration in its original main container.",
            "CONNECTOR_CONFIG_CONFLICT": "Existing connector uses another runner configuration.",
            "MAIN_ENVIRONMENT_REQUIRED": "Register the main runner environment first.",
            "CONFIGURATION_REQUIRED": "Supply runner-config or hub-url/token-file/node/workdir.",
            "CONNECTOR_START_FAILED": "Inspect the private connector log.",
            "CONNECTOR_START_UNCONFIRMED": "Inspect status before starting again.",
            "STARTUP_FILE_EXISTS": "Existing startup script differs; review the new runtime path.",
            "IDENTITY_MISMATCH": "Container UID/GID differs from the hub user identity.",
            "FORBIDDEN_NODE": "The user is not permitted to use this server.",
        }
        code = str(exc).split(":", 1)[0].split(" ", 1)[0] if isinstance(exc, RunnerError) else ""
        if code in hints:
            print(code + ": " + hints[code], file=sys.stderr)
            return 1
        print(
            "CONNECTOR_ERROR: check private configuration, hub API version, identity and access.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
