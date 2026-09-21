"""Local initialization and the single-process hub entry point."""

import argparse
import fcntl
import logging
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .models import Error
from .notifications import Destinations
from .service import Hub
from .store import Store


def main():
    parser = argparse.ArgumentParser(prog="cowork-hub")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=os.getenv("HUB_DATA_DIR"))
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8080)
    sub.add_parser("demo", help="Run a temporary, entirely simulated Worker flow")
    args = parser.parse_args()
    if args.command == "demo":
        from .demo import run

        run()
        return
    if not args.data_dir:
        parser.error("--data-dir or HUB_DATA_DIR is required; choose a host-local disk, not NFS")
    path = Path(args.data_dir).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    # Prevent two CLI instances from running a dispatcher/notifier on the same DB.
    with (path / "hub.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another hub process is already using this data directory")
        hub = Hub(Store(path / "hub.sqlite3"))
        if args.command == "init":
            token_path = path / "admin.token"
            try:
                descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    try:
                        token = hub.bootstrap()
                    except Error:
                        token_path.unlink()
                        raise
                    output.write(token + "\n")
            except (FileExistsError, Error):
                parser.error("Hub is already initialized; existing credentials were preserved")
            print(f"Initialized hub database: {path / 'hub.sqlite3'}")
            print(f"Administrator credential saved privately: {token_path}")
            return
        with hub.store.transaction(write=False) as db:
            if not db.execute("SELECT 1 FROM principals WHERE role='admin'").fetchone():
                parser.error("Run cowork-hub init before serving")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        secret_file = os.getenv("HUB_DISCORD_SECRETS_FILE")
        destinations = Destinations(
            Path(secret_file) if secret_file else None, os.getenv("HUB_DISCORD_GUILD_ID")
        )
        if os.getenv("HUB_DISCORD_DIRECT_USER"):
            destinations.add_environment_webhook(
                user_id=os.getenv("HUB_DISCORD_DIRECT_USER"),
                secret_ref=os.getenv("HUB_DISCORD_DIRECT_REF"),
                channel_id=os.getenv("HUB_DISCORD_DIRECT_CHANNEL"),
                url=os.getenv("DISCORD_WEBHOOK_URL"),
            )
        uvicorn.run(
            create_app(hub, destinations),
            host=args.host,
            port=args.port,
            workers=1,
            access_log=False,
            limit_concurrency=64,
        )
