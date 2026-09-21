#!/usr/bin/env python3
"""Install a downloaded cowork release in a private, explicitly chosen shared directory."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import venv
from pathlib import Path


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
    for path in inputs:
        fingerprint.update(str(path.relative_to(source)).encode() + b"\0" + path.read_bytes())
    source_hash = fingerprint.hexdigest()
    marker = prefix / "installation.json"
    if prefix.exists() or prefix.is_symlink():
        if (
            prefix.is_symlink()
            or prefix.stat().st_uid != os.getuid()
            or prefix.stat().st_mode & 0o077
        ):
            raise RuntimeError("Installation directory must be private and user owned")
        if marker.is_file() and not marker.is_symlink():
            value = json.loads(marker.read_text())
            if (
                value.get("complete")
                and value.get("source_hash") == source_hash
                and (prefix / "venv/bin/cowork-connector").is_file()
            ):
                return {"installed": True, "reused": True, "bin": str(prefix / "venv/bin")}
        raise RuntimeError(
            "Directory already exists; use a new empty prefix. Existing files were preserved"
        )
    prefix.mkdir(mode=0o700, parents=True)
    marker.write_text(
        json.dumps({"source": str(source), "source_hash": source_hash, "complete": False}) + "\n"
    )
    marker.chmod(0o600)
    environment = prefix / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = str(environment / "bin/python")
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
    subprocess.run([python, "-m", "pip", "install", *options, "--no-deps", str(source)], check=True)
    subprocess.run(
        [str(environment / "bin/cowork-connector"), "--help"], check=True, stdout=subprocess.DEVNULL
    )
    marker.write_text(
        json.dumps({"source": str(source), "source_hash": source_hash, "complete": True}) + "\n"
    )
    return {"installed": True, "reused": False, "bin": str(environment / "bin")}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        required=True,
        help="New private installation directory visible at the same path on SSH targets",
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
