#!/usr/bin/env python3
"""Register the approved inventory inside the existing Compose hub.

The host entry point needs only Python's standard library. Credentials stay in
the hub's private data volume. This does not install Workers or verify containers.
"""

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path


class RegistrationError(Exception):
    pass


def prepare(manifest):
    """Turn explicit budgets into node payloads; unknown GPUs remain unregistered."""
    if manifest.get("schema_version") != 1:
        raise RegistrationError("Unsupported registration manifest version")
    if manifest.get("budget_policy", {}).get("source") != "user_confirmed":
        raise RegistrationError("Resource budgets must be confirmed before registration")
    nodes, warnings = [], []
    for item in manifest["nodes"]:
        budget = item["budget"]
        if type(budget["cpus"]) is not int or type(budget["memory_mib"]) is not int:
            raise RegistrationError("Each node needs an explicit CPU and memory budget")
        if budget["cpus"] <= 0 or budget["memory_mib"] <= 0:
            raise RegistrationError("CPU and memory budgets must be positive")
        observed = item["observed"]
        if (
            budget["cpus"] > observed["logical_cpus_visible"]
            or budget["memory_mib"] > observed["memory_total_kib_visible"] // 1024
        ):
            raise RegistrationError(f"Budget exceeds the observed capacity for {item['id']}")
        inventory = observed["gpus_visible"]
        selected = budget["gpu_ids"]
        if inventory is None:
            if selected not in (None, []):
                raise RegistrationError("Cannot register unobserved GPUs")
            gpus = []
            warnings.append(
                f"{item['id']}: GPU inventory is unknown; only CPU/RAM will be registered"
            )
        else:
            known = {gpu["id"]: gpu for gpu in inventory}
            if (
                len(known) != len(inventory)
                or selected is None
                or len(set(selected)) != len(selected)
            ):
                raise RegistrationError("GPU inventory or selection is invalid")
            if not set(selected) <= known.keys():
                raise RegistrationError("Selected GPU is absent from the observed inventory")
            gpus = [known[gpu_id] for gpu_id in sorted(selected)]
        nodes.append(
            {
                "id": item["id"],
                "cpus": budget["cpus"],
                "memory_mib": budget["memory_mib"],
                "gpus": gpus,
            }
        )
    identifiers = [node["id"] for node in nodes]
    if not nodes or len(set(identifiers)) != len(nodes):
        raise RegistrationError("Node IDs must be nonempty and unique")
    if set(identifiers) != set(manifest["requested_allowed_nodes"]):
        raise RegistrationError("Requested grants must match the registration inventory")
    return nodes, warnings


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RegistrationError("Registration credential directory must be private (mode 0700)")


def _credential(path):
    """Save a credential before committing its hash, allowing interrupted retries."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RegistrationError(
                "An existing credential file is not a private regular file"
            ) from None
        token = path.read_text().strip()
        if len(token) < 32:
            raise RegistrationError("An existing credential file is invalid") from None
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w") as output:
        output.write(token + "\n")
        output.flush()
        os.fsync(output.fileno())
    # Persist the directory entry as well as the file before the DB commit.
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return token


def register_inside_hub(manifest, data_dir):
    """Local admin import, atomically updating the same resource ledger as the API."""
    from cowork_hub.models import Error, NodeCreate, UserCreate
    from cowork_hub.service import Hub, digest, encode
    from cowork_hub.store import configured_store

    payloads, warnings = prepare(manifest)
    nodes = [NodeCreate.model_validate(payload) for payload in payloads]
    user = UserCreate(id=manifest["user_id"], allowed_nodes=[node.id for node in nodes])
    data_dir = Path(data_dir)
    if not (data_dir / "admin.token").is_file():
        raise RegistrationError("Use the initialized hub container and its existing data directory")
    hub = Hub(configured_store(data_dir))
    try:
        admin = hub.authenticate((data_dir / "admin.token").read_text().strip())
    except Error as exc:
        raise RegistrationError("The local administrator credential is invalid") from exc
    if admin["role"] != "admin":
        raise RegistrationError("The local credential is not an administrator")
    credential_dir = data_dir / "registration-credentials"
    credentials, results = [], []

    with hub.store.transaction() as db:
        # Check the complete batch before modifying nodes, grants, or credentials.
        existing_nodes = {}
        for node in nodes:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (node.id,)).fetchone()
            existing_nodes[node.id] = row
            if row and (
                row["cpus"] != node.cpus
                or row["memory_mib"] != node.memory_mib
                or sorted(json.loads(row["gpus"]), key=lambda gpu: gpu["id"])
                != sorted([gpu.model_dump() for gpu in node.gpus], key=lambda gpu: gpu["id"])
            ):
                raise RegistrationError(
                    f"Existing node {node.id} has a different budget or GPU inventory; nothing changed"
                )
        expected = [
            (f"worker:{node.id}", "worker", node.id, f"worker-{node.id}.token") for node in nodes
        ]
        expected.append((user.id, "user", None, f"user-{user.id}.token"))
        existing_principals = {}
        for principal_id, role, node_id, _filename in expected:
            row = db.execute("SELECT * FROM principals WHERE id=?", (principal_id,)).fetchone()
            existing_principals[principal_id] = row
            if row and (row["role"] != role or row["node_id"] != node_id or not row["enabled"]):
                raise RegistrationError("An existing principal conflicts with this registration")
            if node_id and bool(row) != bool(existing_nodes[node_id]):
                raise RegistrationError("Existing node and Worker credentials do not match")
        _private_directory(credential_dir)
        for principal_id, role, node_id, filename in expected:
            row = existing_principals[principal_id]
            path = credential_dir / filename
            if row:
                if path.exists():
                    if not secrets.compare_digest(digest(_credential(path)), row["token_hash"]):
                        raise RegistrationError(
                            "Saved credential does not match the existing principal"
                        )
                    credentials.append({"principal": principal_id, "path": str(path)})
                else:
                    warnings.append(
                        f"{principal_id}: existing credential retained; no new token issued"
                    )
            else:
                token = _credential(path)
                db.execute(
                    "INSERT INTO principals(id,role,token_hash,node_id) VALUES(?,?,?,?)",
                    (principal_id, role, digest(token), node_id),
                )
                credentials.append({"principal": principal_id, "path": str(path)})
        for node in nodes:
            if existing_nodes[node.id] is None:
                db.execute(
                    "INSERT INTO nodes(id,cpus,memory_mib,gpus) VALUES(?,?,?,?)",
                    (
                        node.id,
                        node.cpus,
                        node.memory_mib,
                        encode([g.model_dump() for g in node.gpus]),
                    ),
                )
            results.append(
                {
                    "id": node.id,
                    "status": "existing" if existing_nodes[node.id] else "created",
                    "cpus": node.cpus,
                    "memory_mib": node.memory_mib,
                    "gpu_count": len(node.gpus),
                }
            )
        # Preserve unrelated existing grants; this invocation only adds approved nodes.
        db.executemany(
            "INSERT INTO grants(user_id,node_id) VALUES(?,?) ON CONFLICT(user_id,node_id) DO NOTHING",
            [(user.id, node.id) for node in nodes],
        )
    return {
        "status": "registered",
        "nodes": results,
        "user_id": user.id,
        "allowed_nodes_added": user.allowed_nodes,
        "credentials": credentials,
        "warnings": warnings,
        "containers_verified_by_this_command": False,
        "workers_started_by_this_command": False,
    }


def main():
    if sys.argv[1:] == ["--inside-hub"]:
        manifest = globals()["_REGISTRATION_PAYLOAD"]
        report = register_inside_hub(manifest, os.environ.get("HUB_DATA_DIR", "/data"))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=project / "config/cluster-registration.json"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Register inside the running Compose hub"
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    nodes, warnings = prepare(manifest)
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "preview",
                    "nodes": nodes,
                    "user_id": manifest["user_id"],
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    # Use the operator's existing Docker context, without extracting the admin token.
    source = (
        "import json\n_REGISTRATION_PAYLOAD = json.loads("
        + repr(json.dumps(manifest))
        + ")\n"
        + Path(__file__).read_text()
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "hub", "python", "-", "--inside-hub"],
        input=source,
        text=True,
        cwd=project,
        capture_output=True,
    )
    if result.returncode:
        print(
            "Registration did not complete. Run this command in the terminal where the hub's docker compose works.",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    report = json.loads(result.stdout)
    if report.get("status") != "registered":
        raise RegistrationError("Hub did not return a successful registration report")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Only a confirmed hub response changes local registration status.
    manifest["status"] = "nodes_registered_environments_unverified"
    for node in manifest["nodes"]:
        node["registration_status"] = "node_registered_environment_unverified"
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (RegistrationError, OSError, ValueError, KeyError) as exc:
        # Never print credential values or dumps of configuration inputs.
        detail = str(exc) if isinstance(exc, RegistrationError) else type(exc).__name__
        print(f"Registration failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
