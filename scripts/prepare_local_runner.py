#!/usr/bin/env python3
"""Prepare this container's private runner configuration without registering or running a job."""

import argparse
import os
import sys
import uuid
from pathlib import Path

from cowork_hub.models import LocalEnvironment
from cowork_hub.runner import Client, RunnerError, read_private, save


def main():
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ssh-target", default="local")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        config = json.loads(read_private(output))
        if (config["environment"]["node_id"], config["hostname"], config["hub_url"]) != (
            args.node,
            os.uname().nodename,
            args.hub_url,
        ):
            raise RunnerError("Existing configuration differs; use a different output file")
        print(f"Existing configuration preserved: {output}")
        return
    config = {
        "hub_url": args.hub_url,
        "token_file": str(args.token_file.resolve()),
        "hostname": os.uname().nodename,
    }
    client = Client(config)
    try:
        nodes = client.request("GET", "/v1/cluster")
    finally:
        client.close()
    node = next((n for n in nodes if n["id"] == args.node), None)
    if node is None:
        raise RunnerError("Node is not in your allowed server list")
    # Preparation intentionally enables CPU only. GPU visibility must be checked on its target container.
    memory = next(
        int(line.split()[1]) // 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    environment = LocalEnvironment(
        name=f"{args.node}-{os.uname().nodename}",
        node_id=args.node,
        ssh_target=args.ssh_target,
        workdir=str(Path.cwd()),
        instance_id=uuid.uuid4().hex,
        uid=os.getuid(),
        gid=os.getgid(),
        cpus=min(os.cpu_count() or 1, node["cpus"]),
        memory_mib=min(memory, node["memory_mib"]),
        gpu_ids=[],
    )
    config["environment"] = environment.model_dump()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    save(output, config)
    print(f"Prepared configuration: {output}")
    print(f"Node: {args.node}; UID:GID: {os.getuid()}:{os.getgid()}; GPU: disabled")
    print("No environment was activated and no job was submitted.")


if __name__ == "__main__":
    try:
        main()
    except (RunnerError, ValueError, OSError):
        print(
            "Could not prepare runner configuration; check private token, hub access and node grant.",
            file=sys.stderr,
        )
        sys.exit(1)
