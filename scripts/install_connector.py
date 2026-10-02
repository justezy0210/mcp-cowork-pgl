#!/usr/bin/env python3
"""Install a downloaded cowork release in a private, explicitly chosen shared directory."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def read_installation(prefix, source):
    """Only resume a private directory created by this installer for this source path."""
    prefix, source = Path(prefix).absolute(), Path(source).absolute()
    if not prefix.exists() and not prefix.is_symlink():
        return None
    info = prefix.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Installation directory must be private and user owned")
    try:
        fd = os.open(prefix / "installation.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError
            value = json.loads(stream.read(16385))
        if (
            not isinstance(value, dict)
            or value.get("source") != str(source)
            or type(value.get("complete")) is not bool
            or not isinstance(value.get("source_hash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["source_hash"])
        ):
            raise ValueError
    except (OSError, ValueError):
        raise RuntimeError(
            "Unrecognized installation directory; existing files were preserved"
        ) from None
    return value


def write_installation(prefix, value):
    with tempfile.NamedTemporaryFile(
        mode="w", dir=prefix, prefix=".installation-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(prefix / "installation.json")
        finally:
            temporary.unlink(missing_ok=True)


def install(prefix, source, *, wheelhouse=None):
    prefix = Path(prefix).expanduser().absolute()
    source = Path(source).absolute()
    if sys.version_info < (3, 12) or sys.platform != "linux":
        raise RuntimeError("Linux and Python 3.12+ are required")
    if (
        not (source / "pyproject.toml").is_file()
        or not (source / "requirements-client.txt").is_file()
    ):
        raise RuntimeError("Use an unpacked release or a complete source checkout")
    fingerprint = hashlib.sha256()
    inputs = [source / "pyproject.toml", source / "requirements-client.txt"]
    inputs += sorted((source / "src/cowork_hub").glob("*.py"))
    inputs += sorted(path for path in (source / "src/cowork_hub/web").rglob("*") if path.is_file())
    for path in inputs:
        fingerprint.update(str(path.relative_to(source)).encode() + b"\0" + path.read_bytes())
    source_hash = fingerprint.hexdigest()
    previous = read_installation(prefix, source)
    if (
        previous
        and previous["complete"]
        and previous["source_hash"] == source_hash
        and all(
            (prefix / "venv/bin" / name).is_file()
            for name in ("python", "cowork-connector", "cowork-mcp", "cowork-run")
        )
    ):
        return {"installed": True, "reused": True, "bin": str(prefix / "venv/bin")}
    if previous is None:
        prefix.mkdir(mode=0o700, parents=True)
    value = {"source": str(source), "source_hash": source_hash, "complete": False}
    write_installation(prefix, value)
    environment = prefix / "venv"
    if environment.is_symlink():
        raise RuntimeError("Virtual environment must not be a symlink")
    if not (environment / "bin/python").is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = str(environment / "bin/python")
    # A previous attempt may have stopped after creating Python but before bootstrapping pip.
    subprocess.run([python, "-m", "ensurepip", "--upgrade"], check=True, stdout=subprocess.DEVNULL)
    options = ["--no-index", "--find-links", str(Path(wheelhouse).absolute())] if wheelhouse else []
    subprocess.run(
        [
            python,
            "-m",
            "pip",
            "install",
            *options,
            "--require-hashes",
            "-r",
            str(source / "requirements-client.txt"),
        ],
        check=True,
    )
    # Build backend isolation is retained; --no-deps preserves the locked runtime versions.
    subprocess.run(
        [python, "-m", "pip", "install", *options, "--no-deps", "--force-reinstall", str(source)],
        check=True,
    )
    subprocess.run(
        [str(environment / "bin/cowork-connector"), "--help"], check=True, stdout=subprocess.DEVNULL
    )
    write_installation(prefix, {**value, "complete": True})
    return {"installed": True, "reused": False, "bin": str(environment / "bin")}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        required=True,
        help="Private installation directory; recognized installations can be resumed or updated",
    )
    parser.add_argument(
        "--wheelhouse", type=Path, help="Optional offline wheel directory, including build backend"
    )
    args = parser.parse_args()
    try:
        result = install(
            args.prefix, Path(__file__).resolve().parents[1], wheelhouse=args.wheelhouse
        )
        print(json.dumps(result))
        print("Next: cowork-connector configure --help. No credentials or services were changed.")
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("INSTALL_FAILED: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
