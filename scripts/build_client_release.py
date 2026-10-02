#!/usr/bin/env python3
"""Build a source release from an allowlist; never include private lab files."""

import argparse
import hashlib
import tarfile
import tomllib
from pathlib import Path


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
    name = "cowork-hub-" + version
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (name + ".tar.gz")
    selected = [
        source / f
        for f in (
            "pyproject.toml",
            "uv.lock",
            "requirements-client.txt",
            "README.md",
            "Dockerfile",
            "compose.yaml",
            "compose.postgres.yaml",
            ".dockerignore",
            "config/web.example.json",
        )
    ]
    selected += sorted((source / "scripts").glob("*.py"))
    selected += sorted((source / "docs").glob("*.md"))
    selected += sorted((source / "plans").glob("*.md"))
    selected += sorted((source / "src/cowork_hub").glob("*.py"))
    selected += sorted(
        path for path in (source / "src/cowork_hub/web").rglob("*") if path.is_file()
    )
    selected += sorted((source / "skills/cowork-jobs").rglob("*.md"))
    selected += sorted((source / "skills/cowork-jobs").rglob("*.yaml"))
    frontend = source / "frontend"
    selected += [frontend / name for name in (
        "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
        "components.json", "index.html",
    )]
    for directory in ("src", "public"):
        selected += sorted(path for path in (frontend / directory).rglob("*") if path.is_file())
    for path in selected:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Release input is missing or a symlink: " + str(path))
    with archive.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz") as package:
            for path in selected:
                info = package.gettarinfo(str(path), name + "/" + str(path.relative_to(source)))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as data:
                    package.addfile(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    with (output / (archive.name + ".sha256")).open("x") as stream:
        stream.write(checksum + "  " + archive.name + "\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output))
