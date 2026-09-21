"""Wait for a hub reservation, run locally, and durably report the actual outcome."""

import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .models import JobSpec, JobSubmit


class RunnerError(Exception):
    pass


class Retryable(RunnerError):
    pass


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RunnerError("Use a user-owned private file (mode 0600)")
        data = stream.read(262145)
        if len(data) > 262144:
            raise RunnerError("Private file is too large")
        return data


def save(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class Client:
    def __init__(self, config):
        url = urlsplit(config["hub_url"])
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise RunnerError("Invalid hub origin")
        token = read_private(config["token_file"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", token):
            raise RunnerError("Invalid token file")
        self.http = httpx.Client(
            base_url=config["hub_url"].rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=3,
            trust_env=False,
            follow_redirects=False,
        )

    def request(self, method, path, body=None):
        try:
            r = self.http.request(method, path, json=body)
        except httpx.HTTPError:
            raise Retryable("HUB_UNREACHABLE") from None
        if r.status_code >= 500 or r.status_code == 429:
            raise Retryable("HUB_TEMPORARILY_UNAVAILABLE")
        if not 200 <= r.status_code < 300:
            code = "HUB_REQUEST_REJECTED"
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                candidate = r.json().get("error", {}).get("code", "")
                if re.fullmatch(r"[A-Z_]{1,64}", candidate):
                    code = candidate
            raise RunnerError(f"{code} (HTTP {r.status_code})")
        try:
            return r.json()
        except ValueError:
            raise Retryable("HUB_INVALID_RESPONSE") from None

    def close(self):
        self.http.close()


def load_config(path):
    config = json.loads(read_private(path))
    if (config["hostname"], config["environment"]["uid"], config["environment"]["gid"]) != (
        os.uname().nodename,
        os.getuid(),
        os.getgid(),
    ):
        raise RunnerError("Configuration belongs to a different container or UID/GID")
    return config


def identity(config, runner_id):
    return {
        "instance_id": config["environment"]["instance_id"],
        "runner_id": runner_id,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }


def preflight(client, config):
    capabilities = client.request("GET", "/v1/capabilities")
    if capabilities.get("local_runner") != 1:
        raise RunnerError("Upgrade the hub to support local runners")
    if not config.get("environment_id"):
        raise RunnerError("Register and approve this local environment first")
    environments = client.request("GET", "/v1/environments")
    env = next((e for e in environments if e["id"] == config["environment_id"]), None)
    if not env:
        raise RunnerError("ENVIRONMENT_NOT_VERIFIED: environment is not registered")
    if env["status"] != "READY":
        raise RunnerError("ENVIRONMENT_NOT_APPROVED: environment is not approved/ready")
    if not client.request("GET", "/v1/notifications")["configured"]:
        raise RunnerError(
            "NOTIFICATION_NOT_CONFIGURED: configure the hub Discord destination first"
        )


def launch(config_path, argv, cpus, memory_mib, gpu_count=0, *, workdir=None, name=None):
    """Manual submissions receive a fresh request key; MCP supplies a stable retry key."""
    config = load_config(Path(config_path))
    if not config.get("environment_id"):
        raise RunnerError("Register and approve this local environment first")
    request = JobSubmit(
        request_key=uuid.uuid4().hex,
        spec=JobSpec(
            name=name or Path(argv[0]).name,
            environment_ids=[config["environment_id"]],
            argv=argv,
            cpus=cpus,
            memory_mib=memory_mib,
            gpu_count=gpu_count,
            workdir=str(Path(workdir or os.getcwd()).resolve()),
        ),
    )
    return submit_local(config_path, request)


def submit_local(config_path, request: JobSubmit, *, hub_url=None, token_file=None):
    """Durably deduplicate submissions, then leave execution to a detached supervisor.

    An interrupted/unacknowledged launch is never blindly relaunched. Retrying the same
    request returns the existing job ID or a pending error, never a second execution.
    """
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    if hub_url is not None and config["hub_url"].rstrip("/") != hub_url.rstrip("/"):
        raise RunnerError("RUNNER_CONFIG_MISMATCH")
    if (
        token_file is not None
        and Path(config["token_file"]).resolve() != Path(token_file).resolve()
    ):
        raise RunnerError("RUNNER_CONFIG_MISMATCH")
    if request.spec.environment_ids != [config.get("environment_id")]:
        raise RunnerError("LOCAL_ENVIRONMENT_REQUIRED")
    if request.spec.workdir is None:
        raise RunnerError("WORKDIR_REQUIRED")
    client = Client(config)
    try:
        preflight(client, config)
    finally:
        client.close()
    directory = config_path.parent / "runs"
    directory.mkdir(mode=0o700, exist_ok=True)
    if (
        directory.is_symlink()
        or directory.stat().st_uid != os.geteuid()
        or directory.stat().st_mode & 0o077
    ):
        raise RunnerError("Run directory must be private and user owned")
    key = hashlib.sha256(request.request_key.encode()).hexdigest()
    record = directory / ("request-" + key + ".json")
    payload = request.model_dump()
    fd = os.open(record.with_suffix(".submit.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError("SUBMISSION_PENDING: retry with the same request_key") from None
        reused = record.exists()
        if reused:
            state = json.loads(read_private(record))
            previous = {k: v for k, v in state["request"].items() if k != "runner"}
            if previous != payload or state["config"] != str(config_path):
                raise RunnerError("IDEMPOTENCY_CONFLICT")
        else:
            runner_id = uuid.uuid4().hex
            state = {
                "config": str(config_path),
                "runner_id": runner_id,
                "phase": "WAITING",
                "events": [],
                "request": {**payload, "runner": identity(config, runner_id)},
                "job_id": None,
                "log_path": str(record.with_suffix(".log")),
            }
            save(record, state)
            try:
                with open(state["log_path"], "x") as log:
                    os.chmod(state["log_path"], 0o600)
                    subprocess.Popen(
                        [sys.executable, "-m", "cowork_hub.runner", "_supervise", str(record)],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                        close_fds=True,
                    )
            except OSError:
                state.update(phase="ERROR", error="LOCAL_LAUNCH_FAILED")
                save(record, state)
                raise RunnerError("LOCAL_LAUNCH_FAILED") from None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        snapshot = json.loads(read_private(record))
        if snapshot.get("job_id"):
            return {
                "job_id": snapshot["job_id"],
                "record": str(record),
                "log_path": state["log_path"],
                "reused": reused,
            }
        if snapshot["phase"] == "ERROR":
            raise RunnerError(snapshot.get("error", "Submission failed"))
        time.sleep(0.05)
    raise RunnerError(f"SUBMISSION_PENDING: inspect {record}; retry only with the same request_key")


def children(pid):
    try:
        values = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except (FileNotFoundError, ProcessLookupError):
        return []
    result = []
    for value in values:
        child = int(value)
        result.extend(children(child))
        result.append(child)
    return result


def terminate_tree(sig):
    for pid in children(os.getpid()):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def descendants_finished():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid == 0:
            return False


def supervise(record):
    record = Path(record)
    lock = open(record.with_suffix(".lock"), "a")
    os.chmod(lock.name, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 125
    state = json.loads(read_private(record))
    state["supervisor_pid"] = os.getpid()
    save(record, state)
    config = load_config(state["config"])
    runner = identity(config, state["runner_id"])
    runner["claim_id"] = state.get("claim_id")
    client = Client(config)
    proc = None
    cancelled_at = None
    interrupted = False
    leader_code = None
    last_poll = 0.0

    def interrupt(_sig, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    # Linux subreaper keeps orphaned descendants attached to this one job.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RunnerError("Linux child subreaper is required")

    def event(kind, code=None):
        state["events"].append(
            {
                "event_id": uuid.uuid4().hex,
                "execution_id": state["execution_id"],
                "kind": kind,
                "occurred_at": time.time(),
                "exit_code": code,
                "log_path": state["log_path"],
            }
        )
        save(record, state)

    try:
        if state["phase"] in ("CLAIMING", "RUNNING", "RECOVERY_REQUIRED"):
            # A crash between spawn and journal write cannot be distinguished from a live child.
            state["phase"] = "RECOVERY_REQUIRED"
            state["error"] = (
                "Execution state uncertain; reconcile processes before releasing the reservation"
            )
            save(record, state)
            return 125
        if state["phase"] in ("DONE", "ERROR"):
            return state.get("exit_code", 125)
        while True:
            try:
                if not state["job_id"]:
                    job = client.request("POST", "/v1/local/jobs", state["request"])
                    state["job_id"] = job["id"]
                    save(record, state)
                prefix = "/v1/local/jobs/" + state["job_id"]
                while state["events"]:
                    client.request(
                        "POST", prefix + "/events", {"runner": runner, "event": state["events"][0]}
                    )
                    state["events"].pop(0)
                    save(record, state)
                if state["phase"] == "REPORTING":
                    state["phase"] = "DONE"
                    save(record, state)
                    return state["exit_code"]
                if time.monotonic() - last_poll >= 1:
                    job = client.request("POST", prefix + "/poll", runner)
                    last_poll = time.monotonic()
                    state["hub_state"] = job["state"]
                    state["execution_id"] = job["execution_id"]
                    save(record, state)
                    if interrupted:
                        job = client.request("POST", "/v1/jobs/" + job["id"] + "/cancel")
                    if job["cancel_requested"] and cancelled_at is None:
                        cancelled_at = time.monotonic()
                    if state["phase"] == "WAITING":
                        if job["state"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                            state.update(
                                phase="DONE",
                                exit_code=job["exit_code"] if job["exit_code"] is not None else 130,
                            )
                            save(record, state)
                            return state["exit_code"]
                        if cancelled_at is not None and job["execution_id"]:
                            state.update(phase="REPORTING", exit_code=130)
                            event("not_started")
                        elif job["state"] == "DISPATCHING":
                            state["phase"] = "CLAIMING"
                            state["claim_id"] = uuid.uuid4().hex
                            runner["claim_id"] = state["claim_id"]
                            save(record, state)
                            try:
                                grant = client.request("POST", prefix + "/claim", runner)
                            except RunnerError:
                                # No command was spawned in this process. Persist a tombstone before reporting.
                                state.update(phase="REPORTING", exit_code=125)
                                event("not_started")
                                continue
                            if (
                                not grant.get("granted")
                                or grant["execution_id"] != job["execution_id"]
                            ):
                                raise RunnerError("Unexpected execution permission")
                            state["phase"] = "RUNNING"
                            save(record, state)
                            env = dict(os.environ)
                            env["CUDA_VISIBLE_DEVICES"] = ",".join(grant["gpu_ids"])
                            spec = state["request"]["spec"]
                            try:
                                proc = subprocess.Popen(
                                    spec["argv"],
                                    cwd=spec["workdir"] or config["environment"]["workdir"],
                                    env=env,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True,
                                )
                            except OSError:
                                state.update(phase="REPORTING", exit_code=127)
                                event("not_started", 127)
                                continue
                            state["pid"] = proc.pid
                            event("started")
                        elif job["state"] == "UNKNOWN":
                            # This supervisor has not attempted a claim or spawned a child.
                            state.update(phase="REPORTING", exit_code=125)
                            event("not_started")
                    elif state["phase"] == "RUNNING" and job["state"] == "UNKNOWN":
                        event(
                            "started"
                        )  # Reconcile the same observed process, never launch another.
            except Retryable as exc:
                state["error"] = str(exc)
                save(record, state)
            except RunnerError as exc:
                state["error"] = str(exc)
                if str(exc).startswith("INVALID_CLAIM") and proc is None:
                    state["phase"] = "RECOVERY_REQUIRED"
                    save(record, state)
                    return 125
                if state["phase"] == "WAITING" and state["job_id"] is None:
                    state["phase"] = "ERROR"
                    save(record, state)
                    return 125
                save(record, state)
            if proc is not None:
                if interrupted and cancelled_at is None:
                    cancelled_at = time.monotonic()
                if cancelled_at is not None:
                    terminate_tree(
                        signal.SIGKILL if time.monotonic() - cancelled_at > 3 else signal.SIGTERM
                    )
                if leader_code is None:
                    leader_code = proc.poll()
                if leader_code is not None and descendants_finished():
                    state.update(
                        phase="REPORTING",
                        exit_code=130 if cancelled_at is not None else leader_code,
                    )
                    event(
                        "cancelled"
                        if cancelled_at is not None
                        else ("succeeded" if leader_code == 0 else "failed"),
                        leader_code,
                    )
                    proc = None
            time.sleep(0.1)
    finally:
        client.close()
        lock.close()


def memory(value):
    match = re.fullmatch(r"([1-9][0-9]*)(MiB|GiB)?", value)
    if not match:
        raise argparse.ArgumentTypeError("Use an integer MiB value, 512MiB, or 8GiB")
    return int(match[1]) * (1024 if match[2] == "GiB" else 1)


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == "_supervise":
        try:
            return supervise(sys.argv[2])
        except Exception:
            print(
                "Runner stopped unexpectedly; preserve the journal and reconcile before retrying.",
                file=sys.stderr,
            )
            return 125
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=os.getenv("COWORK_RUNNER_CONFIG", ".local/runner.json")
    )
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--mem", type=memory, required=True, dest="memory_mib")
    parser.add_argument("--gpus", type=int, default=0)
    parser.add_argument("--name")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        parser.error("A command after -- is required")
    try:
        result = launch(args.config, argv, args.cpus, args.memory_mib, args.gpus, name=args.name)
        print(json.dumps(result), flush=True)
        if args.detach:
            return 0
        while True:
            state = json.loads(read_private(result["record"]))
            if state["phase"] in ("DONE", "ERROR", "RECOVERY_REQUIRED"):
                code = state.get("exit_code", 125)
                return 128 - code if code < 0 else code
            time.sleep(0.2)
    except KeyboardInterrupt:
        print(
            "Monitoring detached; the submitted job continues. Use the hub cancel API to cancel.",
            file=sys.stderr,
        )
        return 130
    except RunnerError as exc:
        print(str(exc), file=sys.stderr)
        return 125
    except (OSError, ValueError):
        # Configuration and HTTP exceptions can contain private values: never echo them wholesale.
        print(
            "Cannot start/monitor the job. Check hub version, private config, environment approval and Discord setup.",
            file=sys.stderr,
        )
        return 125


if __name__ == "__main__":
    sys.exit(main())
