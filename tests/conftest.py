import itertools

import pytest

from cowork_hub.models import (
    EnvironmentCreate,
    EnvironmentObservation,
    EnvironmentVerification,
    Heartbeat,
    JobSpec,
    JobSubmit,
    NodeCreate,
    UserCreate,
    WorkerEvent,
)
from cowork_hub.service import Hub
from cowork_hub.store import Store


class Rig:
    def __init__(self, path):
        self.now = 1800000000.0
        self.hub = Hub(Store(path), clock=lambda: self.now, heartbeat_timeout=30)
        self.admin = self.hub.bootstrap()
        self.worker = self.hub.create_node(
            NodeCreate(
                id="A",
                cpus=8,
                memory_mib=16384,
                gpus=[{"id": "GPU-1", "model": "test", "memory_mib": 8192}],
            )
        )["worker_token"]
        self.alice = self.hub.create_user(
            UserCreate(
                id="alice",
                allowed_nodes=["A"],
                identity={"uid": 1000, "gid": 1000},
            )
        )["token"]
        self.bob = self.hub.create_user(
            UserCreate(
                id="bob",
                allowed_nodes=["A"],
                identity={"uid": 1001, "gid": 1000},
            )
        )["token"]
        self.observations = []
        self.env = self.add_environment("alice", "alice-container")
        self.bob_env = self.add_environment("bob", "bob-container")
        for user in ("alice", "bob"):
            self.hub.provision_destination(user, f"{user}-ref", "123" if user == "alice" else "456")
        self.keys = itertools.count()

    def add_environment(self, user, container_id):
        env = self.hub.register_environment(
            user,
            EnvironmentCreate(
                name=container_id,
                node_id="A",
                ssh_target=f"{user}@test",
                workdir="/work",
            ),
        )
        self.hub.verify_environment(
            "A",
            env["id"],
            EnvironmentVerification(
                challenge=env["challenge"],
                container_id=container_id,
                uid=self.hub.user_identity(user)["uid"],
                gid=self.hub.user_identity(user)["gid"],
                cpus=8,
                memory_mib=16384,
                gpu_ids=["GPU-1"],
            ),
        )
        self.observations.append(
            EnvironmentObservation(
                environment_id=env["id"],
                container_id=container_id,
                ready=True,
                gpu_ids=["GPU-1"],
            )
        )
        self.heartbeat()
        return env["id"]

    def heartbeat(self):
        return self.hub.heartbeat("A", Heartbeat(environments=self.observations))

    def spec(self, *, user="alice", **changes):
        return JobSpec.model_validate(
            {
                "name": "analysis",
                "environment_ids": [self.env if user == "alice" else self.bob_env],
                "argv": ["python", "analysis.py", "--threads", "8"],
                "cpus": 8,
                "memory_mib": 8192,
                **changes,
            }
        )

    def submit(self, *, user="alice", key=None, mode="queue_if_unavailable", **changes):
        return self.hub.submit(
            user,
            JobSubmit(
                request_key=key or f"request-{next(self.keys):08}",
                mode=mode,
                spec=self.spec(user=user, **changes),
            ),
        )

    def event(self, job, kind="succeeded", **changes):
        data = {
            "event_id": f"event-{next(self.keys)}",
            "execution_id": job["execution_id"],
            "kind": kind,
            "occurred_at": self.now,
            "exit_code": 0 if kind == "succeeded" else None,
            **changes,
        }
        return self.hub.event("A", job["id"], WorkerEvent.model_validate(data))

    def state(self, job, user="alice"):
        return self.hub.get_job(user, job["id"])["state"]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path / "hub.sqlite3")
