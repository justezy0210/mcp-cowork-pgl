#!/usr/bin/env python3
"""Package the client and guided installer as a standard-library Python zip application."""

import argparse
import importlib.util
import zipfile
from pathlib import Path

BOOTSTRAP = '''import runpy, sys, tempfile, zipfile
from pathlib import Path
if sys.version_info < (3, 12) or sys.platform != "linux":
    raise SystemExit("Linux and Python 3.12+ are required")
with tempfile.TemporaryDirectory(prefix="cowork-setup-") as directory:
    with zipfile.ZipFile(sys.argv[0]) as package:
        for member in package.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit("Invalid installer package")
        package.extractall(directory)
    sys.path.insert(0, str(Path(directory) / "scripts"))
    runpy.run_path(str(Path(directory) / "scripts/setup_client.py"), run_name="__main__")
'''


def build(source, output):
    source, output = Path(source).resolve(), Path(output)
    spec = importlib.util.spec_from_file_location("cowork_setup", source / "scripts/setup_client.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    files = setup.release_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", BOOTSTRAP)
        for path in files:
            archive.write(path, str(path.relative_to(source)))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output))
