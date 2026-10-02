#!/usr/bin/env python3
"""Prepare Firebase Hosting assets with public settings only, in a new directory."""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cowork_hub.web_api import security_headers  # noqa: E402
from cowork_hub.web_auth import WebConfig  # noqa: E402

# Also support callers that load this build script through importlib.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_installer import build as build_installer  # noqa: E402


def build(config_path, output, *, relay=False):
    config = WebConfig.model_validate(json.loads(Path(config_path).read_text()))
    if not relay and not config.api_base_url.startswith("https://"):
        raise ValueError("Firebase Hosting requires an explicit HTTPS hub API URL")
    if relay:
        config.api_base_url = ""
    source = Path(__file__).resolve().parents[1] / "src/cowork_hub/web/dist"
    if not (source / "index.html").is_file() or not (source / "assets").is_dir():
        raise ValueError("Build the React frontend first: npm --prefix frontend ci && npm --prefix frontend run build")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    public = output / "public" if relay else output
    if relay:
        public.mkdir()
    for name in ("index.html", "favicon.svg"):
        shutil.copyfile(source / name, public / name)
    shutil.copytree(source / "assets", public / "assets")
    (public / "config.json").write_text(json.dumps(config.public(), indent=2) + "\n")
    project = Path(__file__).resolve().parents[1]
    build_installer(project, public / "downloads/cowork-setup.pyz")
    deployment = {
        "hosting": {
            "public": "public" if relay else ".",
            "ignore": ["firebase.json", "**/.*"],
            "headers": [{
                "source": "**",
                "headers": [{"key": key, "value": value}
                            for key, value in security_headers(config).items()],
            }],
        }
    }
    if relay:
        functions = output / "functions"
        functions.mkdir()
        for name in ("index.js", "relay.js", "package.json", "package-lock.json"):
            shutil.copyfile(project / "functions" / name, functions / name)
        deployment["functions"] = [{
            "source": "functions", "codebase": "cowork-web", "runtime": "nodejs22",
            "ignore": ["node_modules", ".git", "test", "*.log", ".env.local", ".secret.local"],
        }]
        deployment["hosting"]["rewrites"] = [{
            "source": "/v1/web/**",
            "function": {"functionId": "coworkWeb", "region": "asia-east1"},
        }]
    (output / "firebase.json").write_text(
        json.dumps(deployment, indent=2)
        + "\n"
    )
    return {"output": str(output), "project_id": config.firebase.projectId, "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--relay", action="store_true", help="Include the SSH Cloud Function and same-origin Hosting rewrite")
    args = parser.parse_args()
    try:
        result = build(args.config, args.output, relay=args.relay)
    except (OSError, ValueError):
        parser.exit(
            1, "WEB_BUILD_FAILED: check the configuration and use a new output directory.\n"
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
