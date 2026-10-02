#!/usr/bin/env python3
"""Run beside the live Compose hub to prepare one user's existing MCP credential privately."""

import argparse
import hmac
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
from pathlib import Path

# This runs inside the existing hub image. The credential travels through a captured pipe;
# neither this helper's stdout nor its errors contain the value. The shared store
# selects the configured backend; credential validation uses a read transaction.
READ_USER_TOKEN = r"""
import hashlib, os, re, sys
from cowork_hub.store import configured_store
from pathlib import Path
user = sys.argv[1]
root = Path(os.getenv('HUB_DATA_DIR', '/data'))
raw = (root / 'registration-credentials' / ('user-' + user + '.token')).read_bytes()
if not re.fullmatch(rb'[A-Za-z0-9_-]{20,4096}\n?', raw):
    raise SystemExit(1)
token = raw.strip().decode('ascii')
with configured_store(root).transaction(write=False) as db:
    row = db.execute(
        "SELECT id FROM principals WHERE id=? AND role='user' AND enabled=1 AND token_hash=?",
        (user, hashlib.sha256(token.encode()).hexdigest()),
    ).fetchone()
if not row:
    raise SystemExit(1)
sys.stdout.write(token + '\n')
"""


class PreparationError(Exception):
    pass


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PreparationError(
            "Credential directories must be user-owned directories with mode 0700."
        )


def prepare(project, user, *, run=subprocess.run):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", user):
        raise PreparationError("Invalid hub user ID.")
    if project.stat().st_uid != os.geteuid():
        raise PreparationError(
            "Run as the project owner (ezy / UID 1101 here), not with sudo Python."
        )
    private_directory(project / ".local")
    directory = project / ".local" / "credentials"
    private_directory(directory)
    destination = directory / f"user-{user}.token"
    try:
        result = run(
            ["docker", "compose", "exec", "-T", "hub", "python", "-c", READ_USER_TOKEN, user],
            cwd=project,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError(
            "Run this command in the terminal where the Compose hub is running."
        ) from None
    if result.returncode or not re.fullmatch(rb"[A-Za-z0-9_-]{20,4096}\n?", result.stdout):
        raise PreparationError(
            "Could not read a valid existing user token. Check Compose hub access and the registered user."
        )
    raw = result.stdout.strip() + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".prepare-", suffix=".token", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Publish only after a complete write, and never replace an existing credential.
            os.link(temporary, destination)
        except FileExistsError:
            info = destination.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_size > 4097
                or not hmac.compare_digest(destination.read_bytes(), raw)
            ):
                raise PreparationError(
                    "An existing token file differs or has unsafe permissions; preserved it."
                ) from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="Existing hub user ID, for example ezy")
    args = parser.parse_args()
    try:
        destination = prepare(Path(__file__).resolve().parents[1], args.user)
    except (PreparationError, OSError, sqlite3.Error) as exc:
        # OSError may include filenames, but never echo subprocess output or credential contents.
        message = (
            str(exc)
            if isinstance(exc, PreparationError)
            else "Could not prepare the private token file."
        )
        parser.exit(1, message + "\n")
    print(f"MCP token file ready: {destination} (private; token value not displayed)")


if __name__ == "__main__":
    main()
