#!/usr/bin/env python3
"""Run on the actual Compose hub host to migrate an idle SQLite hub to PostgreSQL.

No passwords, tokens, connection URLs or subprocess diagnostics are printed.
Only the hub and its database are controlled; compute containers are untouched.
"""

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class CommandUncertain(RuntimeError):
    pass


PREFLIGHT = """
import json, os, sqlite3
from pathlib import Path
from cowork_hub.store import SCHEMA_VERSION
p=Path(os.getenv('HUB_DATA_DIR','/data'))
if os.getenv('HUB_DATABASE_URL_FILE') or (p/'postgres.url').exists() or (p/'database-backend').exists():
    raise SystemExit('PostgreSQL is already configured; use its backup/upgrade procedure')
with sqlite3.connect((p/'hub.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    version=db.execute('PRAGMA user_version').fetchone()[0]
    active=db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('DISPATCHING','RUNNING','UNKNOWN')").fetchone()[0]
    queued=db.execute("SELECT COUNT(*) FROM jobs WHERE state='QUEUED'").fetchone()[0]
    assert version==SCHEMA_VERSION, 'Upgrade the SQLite hub to the current schema first'
    assert active==0, 'Wait for active jobs and reconcile UNKNOWN reservations first'
    # Waiting detached runners must also remain uninterrupted during initial cutover.
    assert queued==0, 'Wait for queued jobs to finish or cancel them before cutover'
print(json.dumps({'schema_version':version,'active_jobs':active,'queued_jobs':queued}))
"""

PREPARE = """
import json, os, sys
from pathlib import Path
import psycopg
from psycopg import sql
credentials=json.load(sys.stdin)
admin='postgresql://postgres:'+credentials['admin']+'@postgres:5432/cowork'
url='postgresql://cowork:'+credentials['app']+'@postgres:5432/cowork'
with psycopg.connect(admin,autocommit=True,connect_timeout=10) as db:
    if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='cowork'").fetchone():
        db.execute(sql.SQL('CREATE ROLE cowork LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}').format(sql.Literal(credentials['app'])))
        db.execute('ALTER DATABASE cowork OWNER TO cowork')
# Verify persisted credentials; never reset an existing role's password on retry.
with psycopg.connect(url,connect_timeout=10) as db:
    assert db.execute('SELECT NOT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]
p=Path(os.getenv('HUB_DATA_DIR','/data'))/'postgres.url.pending'
try:
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
except FileExistsError:
    assert not p.is_symlink() and p.read_text().strip()==url, 'Pending configuration differs'
else:
    with os.fdopen(fd,'w') as f:
        f.write(url+'\\n'); f.flush(); os.fsync(f.fileno())
print('PostgreSQL connection verified; private configuration staged')
"""

ACTIVATE = """
import os
from pathlib import Path
p=Path(os.getenv('HUB_DATA_DIR','/data'))
assert not (p/'postgres.url').exists(), 'Already active'
# Link publishes without replacing an existing file. Keep the pending file for recovery.
os.link(p/'postgres.url.pending',p/'postgres.url')
fd=os.open(p/'database-backend',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
    f.write('postgresql\\n'); f.flush(); os.fsync(f.fileno())
fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
print('PostgreSQL activated')
"""

VERIFY = """
import os
from pathlib import Path
from cowork_hub.service import Hub
from cowork_hub.store import configured_store
data=Path(os.getenv('HUB_DATA_DIR','/data'))
store=configured_store(data)
assert store.backend=='postgresql'
assert Hub(store).authenticate((data/'admin.token').read_text().strip())['role']=='admin'
import urllib.request
assert urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5).status==200
print('PostgreSQL hub verified; original administrator credential is valid')
"""


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("PostgreSQL credential directories must be owned by you with mode 0700")


def credential(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError(
                "An existing PostgreSQL credential is not a private regular file"
            ) from None
        value = path.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", value):
            raise RuntimeError("An existing PostgreSQL credential is invalid") from None
        return value
    value = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value


def invoke(command, *, input=None, timeout=180):
    try:
        result = subprocess.run(
            command, cwd=PROJECT, input=input, text=True, capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise CommandUncertain(
            "Docker command timed out; its container may still be running. Check it before retrying"
        ) from None
    except OSError:
        raise RuntimeError(
            "Hub command did not complete; check Docker on the actual hub host"
        ) from None
    if result.returncode:
        raise RuntimeError(
            "Hub command failed; captured output was suppressed to protect credentials"
        )
    return result.stdout.strip()


def commands():
    base = ["docker", "compose", "-f", str(PROJECT / "compose.yaml")]
    local = PROJECT / ".local" / "compose.local-runner.yaml"
    if local.exists():
        base += ["-f", str(local)]
    return base, [*base, "-f", str(PROJECT / "compose.postgres.yaml")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Build, check idle hub, migrate and activate PostgreSQL",
    )
    args = parser.parse_args()
    if not args.apply:
        print("On the actual hub Compose host: python3 scripts/enable_postgres.py --apply")
        print(
            "Requires the current schema and no active, UNKNOWN or queued jobs. Original SQLite and credentials are retained."
        )
        return
    base, compose = commands()
    print("Checking the existing hub and requiring an empty job queue...", flush=True)
    try:
        invoke([*base, "exec", "-T", "hub", "python", "-c", PREFLIGHT])
    except RuntimeError:
        raise RuntimeError(
            "Preflight failed: use the actual SQLite hub host, current schema, with no active/UNKNOWN/queued jobs"
        ) from None
    private_directory(PROJECT / ".local")
    directory = PROJECT / ".local" / "postgres"
    private_directory(directory)
    credentials = {name: credential(directory / (name + "-password")) for name in ("admin", "app")}
    print("Building the PostgreSQL-capable hub and starting its database...", flush=True)
    invoke([*compose, "build", "hub"], timeout=600)
    invoke([*compose, "up", "-d", "--wait", "postgres"])
    ephemeral = [
        *compose,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "python",
        "hub",
        "-c",
    ]
    invoke([*ephemeral, PREPARE], input=json.dumps(credentials))
    print("Stopping the hub briefly for a locked, verified import...", flush=True)
    invoke([*base, "stop", "hub"])
    activation_started = False
    try:
        # Recheck after stopping to catch jobs accepted since the first preflight.
        invoke([*ephemeral, PREFLIGHT])
        report = invoke(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "hub",
                "migrate-postgres",
                "--database-url-file",
                "/data/postgres.url.pending",
            ]
        )
        result = json.loads(report)
        if not result.get("verified"):
            raise RuntimeError("Migration did not return a verified result")
        print("Import verified; rows copied: " + str(sum(result["rows"].values())), flush=True)
        print("SQLite backup: " + result["backup"], flush=True)
        activation_started = True
        invoke([*ephemeral, ACTIVATE])
        invoke([*compose, "up", "-d", "--wait", "hub"])
        invoke([*compose, "exec", "-T", "hub", "python", "-c", VERIFY])
    except Exception as exc:
        if isinstance(exc, CommandUncertain):
            raise RuntimeError(
                "Cutover command timed out with an uncertain result. Keep both databases and inspect one-off Compose containers before restarting or retrying; see docs/postgresql.md."
            ) from None
        if not activation_started:
            # Start the original stopped container (not the newly built image).
            invoke([*base, "start", "hub"])
            raise RuntimeError(
                "Cutover stopped before activation; the original SQLite hub was restarted. PostgreSQL may contain a verified copy; do not overwrite it blindly."
            ) from None
        raise RuntimeError(
            "Activation needs attention. Preserve both databases and private configuration; do not revert to SQLite after PostgreSQL may have accepted writes. Follow docs/postgresql.md."
        ) from None
    print(
        "PostgreSQL migration complete. Existing user tokens and server registrations were preserved."
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(
            str(exc)
            if isinstance(exc, RuntimeError)
            else "PostgreSQL setup failed; private details were suppressed",
            file=sys.stderr,
        )
        sys.exit(1)
