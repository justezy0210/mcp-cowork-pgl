#!/usr/bin/env python3
"""Register one user's connector with the container's existing Supervisor."""

import argparse
import configparser
import fnmatch
import hashlib
import json
import os
import pwd
import shlex
import stat
import subprocess
import tempfile
import time
from pathlib import Path


class AutostartError(Exception):
    pass


def supervisor_layout():
    for name in ("/etc/supervisor/supervisord.conf", "/etc/supervisord.conf"):
        main = Path(name)
        if not main.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
        parser.read(main)
        directory = main.parent / "conf.d"
        includes = parser.get("include", "files", fallback="").replace("%(here)s", str(main.parent))
        patterns = [str(main.parent / p) if not p.startswith("/") else p for p in includes.split()]
        if directory.is_dir() and any(fnmatch.fnmatch(str(directory / "cowork.conf"), p) for p in patterns):
            return main, directory
    raise AutostartError("SUPERVISOR_NOT_CONFIGURED")


def specification(python, config):
    python, config = Path(python).absolute(), Path(config).absolute()
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise AutostartError("PRIVATE_CONFIG_REQUIRED")
    caller = int(os.environ.get("SUDO_UID", os.getuid()))
    if caller not in (0, info.st_uid):
        raise AutostartError("CONFIG_OWNER_MISMATCH")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise AutostartError("PYTHON_NOT_FOUND")
    account = pwd.getpwuid(info.st_uid)
    name = f"cowork-connector-{account.pw_uid}-{hashlib.sha256(str(config).encode()).hexdigest()[:12]}"
    # Supervisor's INI parser treats semicolons as comments even inside command quotes.
    values = (str(python), str(config), account.pw_dir, account.pw_name)
    if any(any(c in value for c in '\n\r\x00;') for value in values):
        raise AutostartError("UNSUPPORTED_SERVICE_PATH")
    command = shlex.join([str(python), "-m", "cowork_hub.connector", "run", "--config", str(config)])
    environment = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "PATH": str(python.parent) + ":/usr/local/bin:/usr/bin:/bin"}
    if any('"' in value or '\\' in value for value in environment.values()):
        raise AutostartError("UNSUPPORTED_SERVICE_PATH")
    content = (
        f"; Managed by Cowork for UID {account.pw_uid}.\n[program:{name}]\n"
        f"command={command.replace('%', '%%')}\nuser={account.pw_uid}\n"
        f"environment={','.join(key + '=' + chr(34) + value.replace('%', '%%') + chr(34) for key, value in environment.items())}\n"
        "autostart=true\nautorestart=true\nstartsecs=0\nstartretries=3\n"
        "stopasgroup=true\nkillasgroup=true\nstopwaitsecs=30\numask=077\n"
        "redirect_stderr=true\nstdout_logfile=AUTO\nstdout_logfile_maxbytes=5MB\nstdout_logfile_backups=2\n"
    )
    return name, content, account


def control(main, *args, required=True):
    result = subprocess.run(["/usr/bin/supervisorctl", "-c", str(main), *args],
                            capture_output=True, text=True, timeout=40)
    if required and result.returncode:
        raise AutostartError("SUPERVISOR_CONTROL_FAILED")
    return result.stdout.strip()


def as_user(account, python, config, action):
    argv = [str(python), "-m", "cowork_hub.connector", action, "--config", str(config)]
    kwargs = {}
    if os.geteuid() == 0:
        kwargs = {"user": account.pw_uid, "group": account.pw_gid,
                  "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid)}
    elif os.geteuid() != account.pw_uid:
        raise AutostartError("CONFIG_OWNER_MISMATCH")
    environment = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "PATH": str(Path(python).parent) + ":/usr/local/bin:/usr/bin:/bin"}
    result = subprocess.run(argv, capture_output=True, text=True, timeout=40, env=environment, **kwargs)
    if result.returncode:
        raise AutostartError("CONNECTOR_CONTROL_FAILED")
    return json.loads(result.stdout)


def install(python, config, *, layout=None):
    """Only load/update this service; never restart Supervisor or unrelated programs."""
    python, config = Path(python).absolute(), Path(config).absolute()
    main, directory = layout or supervisor_layout()
    name, content, account = specification(python, config)
    pid = control(main, "pid")
    if not pid.isdecimal() or int(pid) <= 0:
        raise AutostartError("SUPERVISOR_NOT_RUNNING")
    # Validate the existing connector as its own user before installing a service.
    current = as_user(account, python, config, "status")
    target = directory / (name + ".conf")
    # Publish a complete file atomically; a failed write must not break Supervisor on its next boot.
    with tempfile.TemporaryDirectory(prefix=".cowork-", dir=directory) as staging:
        staged = Path(staging) / "service.conf"
        with staged.open("x") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(0o600)
        try:
            os.link(staged, target)
        except FileExistsError:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise AutostartError("SERVICE_FILE_CONFLICT") from None
                if stream.read(65537) != content:
                    raise AutostartError("SERVICE_FILE_CONFLICT") from None
    control(main, "reread")
    managed_pid = control(main, "pid", name, required=False)
    if not (current["running"] and managed_pid == str(current["pid"])):
        if current["running"]:
            as_user(account, python, config, "stop")
            deadline = time.monotonic() + 35
            while as_user(account, python, config, "status")["running"]:
                if time.monotonic() > deadline:
                    raise AutostartError("CONNECTOR_STOP_PENDING")
                time.sleep(0.1)
        control(main, "update", name)
        managed_pid = control(main, "pid", name, required=False)
        if not managed_pid.isdecimal() or managed_pid == "0":
            control(main, "start", name)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        current = as_user(account, python, config, "status")
        if current["running"] and control(main, "pid", name, required=False) == str(current["pid"]):
            return {"autostart_configured": True, "manager": "supervisor", "service": name,
                    "service_file": str(target), "supervisor_config": str(main)}
        time.sleep(0.2)
    raise AutostartError("SERVICE_START_UNCONFIRMED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.python, args.config)))
        return 0
    except (AutostartError, OSError, ValueError, KeyError, configparser.Error, subprocess.SubprocessError) as exc:
        # Do not expose subprocess output, credentials, config contents or environment variables.
        code = str(exc) if isinstance(exc, AutostartError) else "AUTOSTART_SETUP_FAILED"
        result = {"autostart_configured": False, "error": code}
        if code == "SUPERVISOR_NOT_CONFIGURED":
            result["message"] = "지원하는 Supervisor 설정을 찾지 못했습니다. 웹의 컨테이너 승인과 별개인 자동 시작 설정 문제입니다. 본인 계정으로 연결 프로그램을 수동 시작하고, 컨테이너 관리자에게 시작 방식에 맞는 자동 시작 설정을 요청하세요."
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
