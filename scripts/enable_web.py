#!/usr/bin/env python3
"""Enable Firebase login on an existing PostgreSQL hub, from its actual Compose host.

Without --apply this only checks the existing hub and configuration.
Credentials and subprocess diagnostics are never printed.
"""

import argparse
import datetime
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WEB_PATH = "/data/web.mcp-cowork-pgl.json"
KEY_PATH = "/data/firebase-admin.mcp-cowork-pgl.json"

PREFLIGHT = """
import json, os, sys, urllib.request
from pathlib import Path
from firebase_admin import credentials
from cowork_hub.web_auth import WebConfig
from cowork_hub.store import configured_store
payload=json.load(sys.stdin)
config=WebConfig.model_validate(payload['config'])
key=payload['credential']
assert config.firebase.projectId=='mcp-cowork-pgl'
assert key['project_id']==config.firebase.projectId
assert key['client_email']=='cowork-hub-web@mcp-cowork-pgl.iam.gserviceaccount.com'
credentials.Certificate(key)
data=Path(os.environ.get('HUB_DATA_DIR','/data'))
assert str(data)=='/data'
store=configured_store(data)
assert store.backend=='postgresql'
with store.transaction(write=False) as db:
    for user in set(config.users.values()) | set(config.admin_users):
        assert db.execute("SELECT id FROM principals WHERE id=? AND role='user' AND enabled=1",(user,)).fetchone()
for variable, expected in payload['environment'].items():
    assert os.environ.get(variable) in (None, '', expected), 'Different web setup already active'
path=Path(payload['environment']['GOOGLE_APPLICATION_CREDENTIALS'])
if path.exists():
    assert not path.is_symlink() and json.loads(path.read_text())==key, 'Different credential exists'
path=Path(payload['environment']['HUB_WEB_CONFIG'])
if path.exists():
    assert not path.is_symlink()
    previous=WebConfig.model_validate_json(path.read_text())
    assert previous.firebase.projectId==config.firebase.projectId
    assert all(config.users.get(uid)==user for uid,user in previous.users.items()), 'Do not remove existing account links'
    assert set(previous.admin_users) <= set(config.admin_users), 'Do not remove existing web administrators'
with urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5) as response:
    assert json.load(response)['database']=='postgresql'
print(json.dumps({'project_id':config.firebase.projectId,'linked_accounts':len(config.users),'database':'postgresql','preflight':'ok'}))
"""

INSTALL = """
import datetime, json, os, sys, tempfile
from pathlib import Path
payload=json.load(sys.stdin)
stamp=datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%fZ')
for variable, content in [('GOOGLE_APPLICATION_CREDENTIALS',payload['credential']),('HUB_WEB_CONFIG',payload['config'])]:
    path=Path(payload['environment'][variable])
    existed=path.exists()
    if existed:
        assert not path.is_symlink()
        old=json.loads(path.read_text())
        if old==content: continue
        assert variable=='HUB_WEB_CONFIG', 'Never replace an existing private key'
        backup=path.with_name(path.name+'.before-'+stamp)
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f: f.write(path.read_text()); f.flush(); os.fsync(f.fileno())
    fd,name=tempfile.mkstemp(prefix='.web-stage-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(content,f); f.write('\\n'); f.flush(); os.fsync(f.fileno())
        if existed:
            os.replace(name,path)
        else:
            os.link(name,path)
            os.unlink(name)
    finally:
        if os.path.exists(name): os.unlink(name)
print('Web configuration and credential installed privately')
"""

VERIFY = """
import json, urllib.request, urllib.error
with urllib.request.urlopen('http://127.0.0.1:8080/web/config.json',timeout=5) as response:
    data=json.load(response)
assert data['enabled'] and data['firebase']['projectId']=='mcp-cowork-pgl'
assert 'users' not in data
try:
    urllib.request.urlopen('http://127.0.0.1:8080/v1/web/me',timeout=5)
except urllib.error.HTTPError as error:
    assert error.code==401
else:
    raise RuntimeError('Anonymous web access was not rejected')
print('Web login enabled; anonymous token API access rejected')
"""


def private_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise RuntimeError("Configuration and credential files must be owned by you, mode 0600")
    return json.loads(path.read_text())


def invoke(args, *, payload=None, output=None, timeout=45):
    try:
        result = subprocess.run(
            args,
            cwd=PROJECT,
            input=json.dumps(payload).encode() if payload else None,
            stdout=output if output is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "Command failed or timed out; inspect the current hub state before retrying"
        ) from None
    return result.stdout.decode().strip() if output is None else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT / ".local/web.firebase.pending.json")
    parser.add_argument(
        "--credential", type=Path, default=PROJECT / ".local/firebase-admin.mcp-cowork-pgl.json"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    stage = "existing-hub-check"
    try:
        local = PROJECT / ".local/compose.local-runner.yaml"
        compose = [
            "docker",
            "compose",
            "-f",
            str(PROJECT / "compose.yaml"),
            "-f",
            str(local),
            "-f",
            str(PROJECT / "compose.postgres.yaml"),
        ]
        # Never start an empty second hub on the agent's Docker daemon.
        if not invoke([*compose, "ps", "--status", "running", "-q", "hub"]):
            raise RuntimeError("Run this on the actual host where the existing hub is running")
        settings = private_json(local)
        payload = {
            "config": private_json(args.config),
            "credential": private_json(args.credential),
            "environment": {"HUB_WEB_CONFIG": WEB_PATH, "GOOGLE_APPLICATION_CREDENTIALS": KEY_PATH},
        }
        stage = "configuration-preflight"
        print(
            invoke([*compose, "exec", "-T", "hub", "python", "-c", PREFLIGHT], payload=payload),
            flush=True,
        )
        if not args.apply:
            print("Checks passed. Use --apply to back up PostgreSQL and enable web login.")
            return
        # The Compose host may run Python 3.8; container-side code runs on Python 3.12.
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")  # noqa: UP017
        backup_dir = PROJECT / ".local/backups"
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        info = backup_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise RuntimeError("Backup directory must be owned by you, mode 0700")
        stage = "postgresql-backup"
        backup = backup_dir / f"cowork-before-web-{stamp}.dump"
        with backup.open("xb") as stream:
            invoke(
                [
                    *compose,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    "cowork",
                    "-Fc",
                ],
                output=stream,
                timeout=120,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if backup.stat().st_size == 0:
            raise RuntimeError("Database backup is empty")
        with (backup_dir / f"compose-before-web-{stamp}.json").open("x") as stream:
            json.dump(settings, stream, indent=2)
        stage = "install-private-web-files"
        print(
            invoke([*compose, "exec", "-T", "hub", "python", "-c", INSTALL], payload=payload),
            flush=True,
        )
        stage = "update-compose-environment"
        settings["services"]["hub"].setdefault("environment", {}).update(payload["environment"])
        fd, name = tempfile.mkstemp(prefix=".compose-web-", dir=local.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(settings, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, local)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        stage = "restart-existing-hub"
        invoke(
            [
                *compose,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--force-recreate",
                "hub",
            ],
            timeout=120,
        )
        stage = "verify-web-login"
        print(invoke([*compose, "exec", "-T", "hub", "python", "-c", VERIFY]), flush=True)
        print("Database backup: " + str(backup))
        print(
            "Google login, account linking and personal token download still require browser verification."
        )
    except (OSError, ValueError, RuntimeError):
        # Do not print exception details that may include configuration or credentials.
        parser.exit(
            1,
            f"WEB_SETUP_FAILED at {stage}. Check the actual hub host, private files and current service state.\n",
        )


if __name__ == "__main__":
    main()
