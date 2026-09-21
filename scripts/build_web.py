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


def build(config_path, output):
    config = WebConfig.model_validate(json.loads(Path(config_path).read_text()))
    if not config.api_base_url.startswith("https://"):
        raise ValueError("Firebase Hosting requires an explicit HTTPS hub API URL")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "src/cowork_hub/web"
    for name in ("index.html", "style.css", "app.js", "tokens.js", "favicon.svg"):
        shutil.copyfile(source / name, output / name)
    (output / "config.json").write_text(json.dumps(config.public(), indent=2) + "\n")
    (output / "firebase.json").write_text(
        json.dumps(
            {
                "hosting": {
                    "public": ".",
                    "ignore": ["firebase.json", "**/.*"],
                    "headers": [
                        {
                            "source": "**",
                            "headers": [
                                {"key": key, "value": value}
                                for key, value in security_headers(config).items()
                            ],
                        }
                    ],
                }
            },
            indent=2,
        )
        + "\n"
    )
    return {"output": str(output), "project_id": config.firebase.projectId, "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.config, args.output)
    except (OSError, ValueError):
        parser.exit(
            1, "WEB_BUILD_FAILED: check the configuration and use a new output directory.\n"
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
