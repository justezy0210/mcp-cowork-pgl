#!/usr/bin/env python3
"""Run on the existing hub's Compose host: backup/upgrade hub and confirm a local environment.

Docker is used only to update the hub application, never to execute a research job.
Only public registration metadata enters the container. Credentials stay inside /data.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BACKUP = """
import datetime, os, sqlite3
from pathlib import Path
if os.environ.get('HUB_DATABASE_URL_FILE') or (Path(os.environ.get('HUB_DATA_DIR','/data'))/'postgres.url').exists() or (Path(os.environ.get('HUB_DATA_DIR','/data'))/'database-backend').exists():
    raise RuntimeError('Use pg_dump and the PostgreSQL deployment instructions before upgrading')
p=Path(os.environ.get('HUB_DATA_DIR','/data'))
source=sqlite3.connect('file:'+str(p/'hub.sqlite3')+'?mode=ro', uri=True)
target=p/('hub-before-local-runner-'+datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%f')+'.sqlite3')
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(fd)
with sqlite3.connect(target) as backup:
    source.backup(backup)
source.close()
print('Backup saved inside hub volume: '+str(target))
"""
ENABLE = """
import json, os, sys
from pathlib import Path
from cowork_hub.models import LocalEnvironment, UserIdentity
from cowork_hub.notifications import Destinations
from cowork_hub.service import Hub
from cowork_hub.store import configured_store
payload=json.load(sys.stdin)
data=Path(os.environ.get('HUB_DATA_DIR','/data'))
hub=Hub(configured_store(data))
admin=hub.authenticate((data/'admin.token').read_text().strip())
if admin['role'] != 'admin':
    raise RuntimeError('Local administrator credential is required')
request=LocalEnvironment.model_validate(payload['environment'])
user=payload['user']
# The invoking administrator confirms the exact owner, UID/GID and physical node shown by the helper.
hub.set_user_identity(user, UserIdentity(uid=request.uid,gid=request.gid))
result=hub.local.register(user,request)
hub.local.approve(result['id'])
if payload.get('configure_discord'):
    if os.environ.get('HUB_DISCORD_DIRECT_USER') != user:
        raise RuntimeError('Discord destination owner does not match')
    ref=os.environ.get('HUB_DISCORD_DIRECT_REF')
    channel=os.environ.get('HUB_DISCORD_DIRECT_CHANNEL')
    destinations=Destinations(None, os.environ.get('HUB_DISCORD_GUILD_ID'))
    try:
        destinations.add_environment_webhook(user_id=user, secret_ref=ref, channel_id=channel,
                                              url=os.environ.get('DISCORD_WEBHOOK_URL'))
        destinations.verify(user,ref,channel)
    finally:
        destinations.close()
    with hub.store.transaction(write=False) as db:
        previous=db.execute('SELECT secret_ref,channel_id FROM notification_destinations WHERE user_id=? AND enabled=1 ORDER BY id DESC LIMIT 1',(user,)).fetchone()
    if not previous or (previous['secret_ref'],previous['channel_id']) != (ref,channel):
        hub.provision_destination(user,ref,channel)
print(json.dumps({'environment_id':result['id'],'notification_configured':hub.notification_status(user)['configured']}))
"""


def compose_settings(metadata, user):
    if metadata["user_id"] != user or not Path(metadata["env_file"]).is_absolute():
        raise RuntimeError("Discord metadata owner or source path does not match")
    return {
        "services": {
            "hub": {
                "env_file": [metadata["env_file"]],
                "environment": {
                    "HUB_DISCORD_DIRECT_USER": user,
                    "HUB_DISCORD_DIRECT_REF": metadata["secret_ref"],
                    "HUB_DISCORD_DIRECT_CHANNEL": metadata["channel_id"],
                    "HUB_DISCORD_GUILD_ID": metadata["guild_id"],
                },
            }
        }
    }


def inside(code, payload=None, *, compose=None):
    result = subprocess.run(
        [*(compose or ["docker", "compose"]), "exec", "-T", "hub", "python", "-c", code],
        cwd=PROJECT,
        input=json.dumps(payload) if payload else None,
        text=True,
        capture_output=True,
        timeout=40,
    )
    if result.returncode:
        # Captured interpreter errors might include input data or private paths; keep output bounded.
        if code == BACKUP:
            raise RuntimeError(
                "Backup failed; PostgreSQL hubs must use the pg_dump/upgrade procedure in docs/postgresql.md"
            )
        raise RuntimeError(
            "Hub helper failed. Check the hub version, existing account/grants and UID/GID policy."
        )
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument(
        "--discord-metadata",
        type=Path,
        help="Use the verified existing channel and env-file reference",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Back up SQLite and rebuild/recreate only the hub service",
    )
    args = parser.parse_args()
    path = args.config.resolve()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise RuntimeError("Runner configuration must be a private file owned by the invoking user")
    config = json.loads(path.read_text())
    environment = config["environment"]
    if environment["uid"] != os.getuid():
        raise RuntimeError(
            "Invoke this helper as the registered user, without changing config ownership"
        )
    override = PROJECT / ".local" / "compose.local-runner.yaml"
    if args.discord_metadata:
        metadata = json.loads(args.discord_metadata.read_text())
        settings = (
            json.loads(override.read_text()) if override.exists() else {"services": {"hub": {}}}
        )
        updates = compose_settings(metadata, args.user)["services"]["hub"]
        hub_settings = settings.setdefault("services", {}).setdefault("hub", {})
        hub_settings.setdefault("environment", {}).update(updates["environment"])
        hub_settings["env_file"] = list(
            dict.fromkeys([*hub_settings.get("env_file", []), *updates["env_file"]])
        )
        if not os.access(metadata["env_file"], os.R_OK):
            raise RuntimeError("Existing Discord environment file is not readable on this host")
        override.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Contains only a source path and public IDs; never read or copy the webhook here.
        override.write_text(json.dumps(settings, indent=2) + "\n")
        override.chmod(0o600)
    compose = ["docker", "compose", "-f", str(PROJECT / "compose.yaml")]
    if override.exists():
        compose += ["-f", str(override)]
    print(
        f"Confirming local environment: user={args.user}, node={environment['node_id']}, UID:GID={environment['uid']}:{environment['gid']}",
        flush=True,
    )
    if (PROJECT / ".local" / "postgres" / "admin-password").exists():
        compose += ["-f", str(PROJECT / "compose.postgres.yaml")]
    if args.upgrade or args.discord_metadata:
        print(inside(BACKUP, compose=compose), flush=True)
        subprocess.run(
            [*compose, "up", "-d", *(["--build"] if args.upgrade else []), "hub"],
            cwd=PROJECT,
            check=True,
        )
    result = json.loads(
        inside(
            ENABLE,
            {
                "user": args.user,
                "environment": environment,
                "configure_discord": bool(args.discord_metadata),
            },
            compose=compose,
        )
    )
    config["environment_id"] = result["environment_id"]
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print("Environment approved: " + result["environment_id"])
    print("Runner configuration updated: " + str(path))
    print(
        "Discord destination: "
        + (
            "configured"
            if result["notification_configured"]
            else "not configured; production submission remains blocked"
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            str(exc)
            if isinstance(exc, RuntimeError)
            else "Hub setup failed; existing credentials were preserved.",
            file=sys.stderr,
        )
        sys.exit(1)
