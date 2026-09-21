"""Small in-process demonstration; no SSH, Docker, or Discord network requests."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from .api import create_app
from .models import NotificationProvision
from .service import Hub
from .store import Store


class DemoDestinations:
    def verify(self, user_id, secret_ref, channel_id):
        assert (user_id, secret_ref, channel_id) == ("alice", "demo-only", "123")

    def close(self):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="cowork-demo-") as directory:
        hub = Hub(Store(Path(directory) / "hub.sqlite3"))
        admin_token = hub.bootstrap()
        with TestClient(create_app(hub, DemoDestinations(), background=False)) as client:

            def call(method, path, token, body=None):
                response = client.request(
                    method, path, headers={"Authorization": f"Bearer {token}"}, json=body
                )
                response.raise_for_status()
                return response.json()

            node = call(
                "POST",
                "/v1/admin/nodes",
                admin_token,
                {
                    "id": "A",
                    "cpus": 8,
                    "memory_mib": 16384,
                    "gpus": [{"id": "GPU-demo", "model": "Demo GPU", "memory_mib": 8192}],
                },
            )
            user = call(
                "POST", "/v1/admin/users", admin_token, {"id": "alice", "allowed_nodes": ["A"]}
            )
            call(
                "POST",
                "/v1/admin/notifications",
                admin_token,
                NotificationProvision(
                    user_id="alice", secret_ref="demo-only", channel_id="123"
                ).model_dump(),
            )
            env = call(
                "POST",
                "/v1/environments",
                user["token"],
                {
                    "name": "alice-A",
                    "node_id": "A",
                    "ssh_target": "alice@demo",
                    "workdir": "/work",
                },
            )
            call(
                "POST",
                f"/v1/worker/environments/{env['id']}/verify",
                node["worker_token"],
                {
                    "challenge": env["challenge"],
                    "container_id": "demo-container",
                    "uid": 1000,
                    "gid": 1000,
                    "cpus": 8,
                    "memory_mib": 16384,
                    "gpu_ids": ["GPU-demo"],
                },
            )
            call(
                "POST",
                "/v1/worker/heartbeat",
                node["worker_token"],
                {
                    "environments": [
                        {
                            "environment_id": env["id"],
                            "container_id": "demo-container",
                            "ready": True,
                            "gpu_ids": ["GPU-demo"],
                        }
                    ]
                },
            )
            spec = {
                "name": "example",
                "environment_ids": [env["id"]],
                "argv": ["python", "analysis.py"],
                "cpus": 8,
                "memory_mib": 8192,
                "gpu_count": 1,
            }
            first = call(
                "POST", "/v1/jobs", user["token"], {"request_key": "demo-job-1", "spec": spec}
            )
            second = call(
                "POST", "/v1/jobs", user["token"], {"request_key": "demo-job-2", "spec": spec}
            )
            for kind, code in (("started", None), ("succeeded", 0)):
                call(
                    "POST",
                    f"/v1/worker/jobs/{first['id']}/events",
                    node["worker_token"],
                    {
                        "event_id": f"demo-{kind}",
                        "execution_id": first["execution_id"],
                        "kind": kind,
                        "occurred_at": hub.clock(),
                        "exit_code": code,
                    },
                )
            after = call("GET", f"/v1/jobs/{second['id']}", user["token"])
            finished = call("GET", f"/v1/jobs/{first['id']}", user["token"])
            print(
                json.dumps(
                    {
                        "mode": "simulation; no commands executed or Discord messages sent",
                        "first_job": [first["state"], finished["state"]],
                        "second_job": [second["state"], after["state"]],
                        "notifications_recorded": [n["kind"] for n in finished["notifications"]],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
