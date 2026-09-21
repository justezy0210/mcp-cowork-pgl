import concurrent.futures

import pytest

from cowork_hub.models import (
    EnvironmentCreate,
    EnvironmentVerification,
    Error,
    Heartbeat,
    JobSubmit,
    NodeCreate,
    PlanRequest,
    WorkerEvent,
)
from cowork_hub.service import Hub
from cowork_hub.store import Store


def test_finish_releases_resources_and_assigns_next_without_agent(rig):
    first = rig.submit(gpu_count=1)
    second = rig.submit(gpu_count=1)
    assert first["state"] == "DISPATCHING"
    assert first["notifications"] == []
    assert second["state"] == "QUEUED"
    rig.event(first, "started")
    assert rig.state(first) == "RUNNING"
    rig.event(first)
    assert rig.state(first) == "SUCCEEDED"
    assert rig.state(second) == "DISPATCHING"
    with rig.hub.store.transaction(write=False) as db:
        assert db.execute("SELECT job_id FROM gpu_reservations").fetchone()[0] == second["id"]
    assert [n["kind"] for n in rig.hub.get_job("alice", first["id"])["notifications"]] == [
        "started",
        "finished",
    ]


def test_fifo_skips_large_job_but_preserves_feasible_order(rig):
    first = rig.submit(cpus=4)
    large = rig.submit(cpus=8)
    small = rig.submit(cpus=4)
    later = rig.submit(cpus=4)
    assert rig.state(large) == "QUEUED"
    assert rig.state(small) == "DISPATCHING"
    assert rig.state(later) == "QUEUED"
    rig.event(first)
    assert rig.state(large) == "QUEUED"
    assert rig.state(later) == "DISPATCHING"
    rig.event(small)
    rig.event(rig.hub.get_job("alice", later["id"]))
    assert rig.state(large) == "DISPATCHING"


def test_memory_limit_and_gpu_identity_shared_across_users(rig):
    first = rig.submit(cpus=1, memory_mib=14000, gpu_count=1)
    second = rig.submit(user="bob", cpus=1, memory_mib=4000)
    third = rig.submit(user="bob", cpus=1, memory_mib=1, gpu_count=1)
    assert second["state"] == third["state"] == "QUEUED"
    assert rig.hub.cluster("bob")[0]["reserved_cpus"] == 1
    rig.event(first)
    assert rig.state(second, "bob") == rig.state(third, "bob") == "DISPATCHING"


def test_plan_is_read_only_and_reduced_argv_is_explicit(rig):
    rig.submit(cpus=4)
    primary = rig.spec()
    reduced = rig.spec(cpus=4, argv=["python", "analysis.py", "--threads", "4"])
    with rig.hub.store.transaction(write=False) as db:
        before = list(db.iterdump())
    result = rig.hub.plan("alice", PlanRequest(primary=primary, alternatives=[reduced]))
    assert [p["can_start_now"] for p in result["profiles"]] == [False, True]
    assert result["profiles"][1]["spec"]["argv"][-1] == "4"
    with rig.hub.store.transaction(write=False) as db:
        assert list(db.iterdump()) == before


def test_plan_respects_previously_queued_job(rig):
    rig.now += 31  # Submit while the node is offline.
    first = rig.submit()
    assert first["state"] == "QUEUED"
    # A fresh heartbeat is visible just before the scheduler gets its turn.
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE nodes SET last_seen=?", (rig.now,))
    plan = rig.hub.plan("alice", PlanRequest(primary=rig.spec()))
    assert not plan["profiles"][0]["can_start_now"]
    with pytest.raises(Error, match="nothing was queued"):
        rig.submit(mode="start_if_available")
    assert rig.state(first) == "DISPATCHING"
    assert len(rig.hub.list_jobs("alice")) == 1


def test_start_now_never_silently_queues(rig):
    rig.submit()
    with pytest.raises(Error) as caught:
        rig.submit(mode="start_if_available")
    assert caught.value.code == "CAPACITY_UNAVAILABLE"
    assert len(rig.hub.list_jobs("alice")) == 1


def test_impossible_request_rejected_and_no_partial_reservation(rig):
    with pytest.raises(Error) as caught:
        rig.submit(cpus=9, gpu_count=1)
    assert caught.value.code == "UNSATISFIABLE"
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 0
    assert not rig.hub.cluster("alice")[0]["gpus"][0]["reserved"]


def test_idempotency_replay_and_conflict(rig):
    first = rig.submit(key="retry-key")
    again = rig.submit(key="retry-key")
    assert first["id"] == again["id"]
    with pytest.raises(Error) as caught:
        rig.submit(key="retry-key", cpus=4)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(rig.hub.list_jobs("alice")) == 1


def test_concurrent_submissions_cannot_oversubscribe(rig):
    def submit(index):
        return rig.hub.submit(
            "alice",
            JobSubmit(
                request_key=f"concurrent-{index}",
                spec=rig.spec(cpus=4, gpu_count=1),
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(12)))
    assert sum(r["state"] == "DISPATCHING" for r in results) == 1
    assert sum(r["state"] == "QUEUED" for r in results) == 11
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 4


def test_cancellation_holds_reservation_until_worker_confirms(rig):
    first = rig.submit(gpu_count=1)
    second = rig.submit(gpu_count=1)
    cancelled = rig.hub.cancel("alice", first["id"])
    assert cancelled["cancel_requested"]
    assert rig.state(second) == "QUEUED"
    assert rig.hub.cluster("alice")[0]["gpus"][0]["reserved"]
    assert rig.hub.assignments("A")[0]["cancel_requested"]
    rig.event(first, "not_started")
    assert rig.state(first) == "CANCELLED"
    assert rig.state(second) == "DISPATCHING"
    assert [n["kind"] for n in rig.hub.get_job("alice", first["id"])["notifications"]] == [
        "finished"
    ]
    with pytest.raises(Error):
        rig.event(first, "started")


def test_queued_cancellation_never_reports_a_start(rig):
    rig.submit()
    job = rig.submit()
    result = rig.hub.cancel("alice", job["id"])
    assert result["state"] == "CANCELLED"
    assert result["execution_id"] is None
    assert [n["kind"] for n in result["notifications"]] == ["finished"]


def test_disconnect_and_restart_retain_unknown_reservations(rig):
    first = rig.submit(gpu_count=1)
    second = rig.submit(gpu_count=1)
    rig.now += 31
    rig.hub.tick()
    assert rig.state(first) == "UNKNOWN"
    assert rig.state(second) == "QUEUED"
    rig.hub = Hub(Store(rig.hub.store.path), clock=lambda: rig.now, heartbeat_timeout=30)
    rig.heartbeat()
    assert rig.state(first) == "UNKNOWN"
    assert rig.state(second) == "QUEUED"
    assert rig.hub.cluster("alice")[0]["gpus"][0]["reserved"]
    rig.event(first, "started")  # A new reconciliation report, not a replayed event.
    assert rig.state(first) == "RUNNING"
    rig.event(first)
    assert rig.state(second) == "DISPATCHING"


def test_duplicate_and_out_of_order_completion_are_safe(rig):
    first = rig.submit(gpu_count=1)
    second = rig.submit(gpu_count=1)
    event = WorkerEvent(
        event_id="complete-once",
        execution_id=first["execution_id"],
        kind="succeeded",
        occurred_at=rig.now,
        exit_code=0,
    )
    rig.hub.event("A", first["id"], event)
    assert rig.hub.event("A", first["id"], event)["duplicate"]
    rig.event(first, "started", occurred_at=rig.now - 1)
    assert rig.state(first) == "SUCCEEDED"
    assert rig.state(second) == "DISPATCHING"
    with rig.hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM gpu_reservations").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM notifications WHERE job_id=?", (first["id"],)
            ).fetchone()[0]
            == 2
        )
    with pytest.raises(Error):
        rig.event(first, "failed", exit_code=1)


def test_revoked_node_is_skipped_for_queued_jobs(rig):
    first = rig.submit()
    queued = rig.submit()
    rig.hub.set_grants("alice", [])
    rig.event(first)
    assert rig.state(queued) == "QUEUED"
    with pytest.raises(Error) as caught:
        rig.submit()
    assert caught.value.code == "FORBIDDEN_NODE"


def test_changed_container_or_gpu_inventory_stops_new_assignments(rig):
    rig.observations[0].container_id = "replacement-container"
    rig.heartbeat()
    job = rig.submit()
    assert job["state"] == "QUEUED"
    rig.observations[0].container_id = "alice-container"
    rig.heartbeat()
    assert rig.state(job) == "DISPATCHING"


def test_chooses_another_allowed_server_with_a_prepared_environment(rig):
    rig.hub.create_node(NodeCreate(id="B", cpus=8, memory_mib=16384))
    rig.hub.set_grants("alice", ["A", "B"])
    registered = rig.hub.register_environment(
        "alice",
        EnvironmentCreate(
            name="alice-B",
            node_id="B",
            ssh_target="alice@B",
            workdir="/work",
        ),
    )
    rig.hub.verify_environment(
        "B",
        registered["id"],
        EnvironmentVerification(
            challenge=registered["challenge"],
            container_id="alice-B-container",
            uid=1000,
            gid=1000,
            cpus=8,
            memory_mib=16384,
        ),
    )
    rig.hub.heartbeat(
        "B",
        Heartbeat(
            environments=[
                {
                    "environment_id": registered["id"],
                    "container_id": "alice-B-container",
                    "ready": True,
                }
            ]
        ),
    )
    rig.submit()
    next_job = rig.submit(environment_ids=[rig.env, registered["id"]])
    assert next_job["node_id"] == "B"
    assert {n["id"] for n in rig.hub.cluster("bob")} == {"A"}
