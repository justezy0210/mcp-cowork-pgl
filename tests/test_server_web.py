import json

import pytest
from fastapi.testclient import TestClient
from test_web import auth, settings, verify

from cowork_hub.api import create_app
from cowork_hub.models import (
    EnvironmentCreate,
    EnvironmentVerification,
    LocalEnvironment,
    NodeCreate,
)
from cowork_hub.server_management import ServerManagement
from cowork_hub.store import SCHEMA_VERSION


@pytest.fixture
def web(rig):
    app = create_app(
        rig.hub, background=False, web_config=settings(admin_users=["alice"]), web_verify=verify
    )
    with TestClient(app) as client:
        yield client


def test_server_data_and_admin_routes_are_authorized_by_google_identity(rig, web):
    rig.hub.create_node(NodeCreate(id="PRIVATE", cpus=32, memory_mib=10000))
    rig.submit(user="bob", name="private-job", argv=["private-command"])
    for route in [
        "/profile",
        "/servers",
        "/jobs",
        "/access-requests",
        "/environments",
        "/admin/users",
    ]:
        assert web.get("/v1/web" + route).status_code == 401
        assert web.get("/v1/web" + route, headers=auth(rig.admin)).status_code == 401
    assert web.get("/v1/web/profile", headers=auth()).json()["is_admin"]
    assert not web.get("/v1/web/profile", headers=auth("google-bob")).json()["is_admin"]
    assert [n["id"] for n in web.get("/v1/web/servers", headers=auth()).json()] == ["A"]
    assert "PRIVATE" in web.get("/v1/web/admin/servers", headers=auth()).text
    assert "private-job" not in web.get("/v1/web/jobs", headers=auth()).text
    for route in ["/servers", "/users", "/access-requests", "/environments/pending", "/audit"]:
        assert web.get("/v1/web/admin" + route, headers=auth("google-bob")).status_code == 403
    assert (
        web.post(
            "/v1/web/admin/users/bob/grants",
            headers=auth("google-bob"),
            json={"allowed_nodes": ["PRIVATE"], "expected_nodes": ["A"]},
        ).status_code
        == 403
    )
    assert "admin_users" not in web.get("/web/config.json").text


def test_access_request_persists_deduplicates_and_requires_admin_approval(rig, web):
    rig.hub.create_node(NodeCreate(id="B", cpus=8, memory_mib=16000))
    body = {
        "node_id": "B",
        "uid": 1001,
        "gid": 1000,
        "port": 11019,
        "request_key": "test-request-1",
    }
    response = web.post("/v1/web/access-requests", headers=auth("google-bob"), json=body)
    assert response.status_code == 201
    request = response.json()
    assert request["state"] == "PENDING"
    assert [n["id"] for n in rig.hub.cluster("bob")] == ["A"]
    assert (
        web.post("/v1/web/access-requests", headers=auth("google-bob"), json=body).json()["id"]
        == request["id"]
    )
    assert (
        web.post(
            "/v1/web/access-requests", headers=auth("google-bob"), json={**body, "port": 22}
        ).status_code
        == 409
    )
    assert (
        web.post(
            "/v1/web/access-requests",
            headers=auth("google-bob"),
            json={**body, "request_key": "test-request-2"},
        ).status_code
        == 409
    )
    assert web.get("/v1/web/access-requests", headers=auth()).json()["total"] == 0
    assert (
        ServerManagement(type(rig.hub)(rig.reopen_store())).access_requests("bob", 1, 20)["total"]
        == 1
    )
    route = "/v1/web/admin/access-requests/" + request["id"] + "/review"
    assert (
        web.post(route, headers=auth("google-bob"), json={"decision": "APPROVED"}).status_code
        == 403
    )
    assert (
        web.post(route, headers=auth(), json={"decision": "APPROVED"}).json()["state"] == "APPROVED"
    )
    assert web.post(route, headers=auth(), json={"decision": "APPROVED"}).status_code == 200
    assert web.post(route, headers=auth(), json={"decision": "REJECTED"}).status_code == 409
    assert [n["id"] for n in rig.hub.cluster("bob")] == ["A", "B"]
    assert (
        web.post(
            "/v1/web/access-requests", headers=auth("google-bob"), json={**body, "user_id": "alice"}
        ).status_code
        == 422
    )
    history = web.get("/v1/web/admin/audit", headers=auth()).json()
    assert history["total"] == 3
    assert {"access.request", "access.review", "grants.update"} == {
        r["action"] for r in history["items"]
    }
    assert "token" not in json.dumps(history)


def test_rejected_request_does_not_grant_access_and_wrong_identity_is_rejected(rig, web):
    rig.hub.create_node(NodeCreate(id="B", cpus=8, memory_mib=16000))
    body = {"node_id": "B", "uid": 1000, "gid": 1000, "port": 22, "request_key": "test-request-1"}
    assert (
        web.post("/v1/web/access-requests", headers=auth("google-bob"), json=body).status_code
        == 409
    )
    r = web.post(
        "/v1/web/access-requests", headers=auth("google-bob"), json={**body, "uid": 1001}
    ).json()
    assert (
        web.post(
            "/v1/web/admin/access-requests/" + r["id"] + "/review",
            headers=auth(),
            json={"decision": "REJECTED"},
        ).status_code
        == 200
    )
    assert [n["id"] for n in rig.hub.cluster("bob")] == ["A"]


def test_permission_and_budget_changes_preserve_reservations_and_detect_stale_edits(rig, web):
    job = rig.submit(cpus=4, memory_mib=8000)
    original = {"cpus": 8, "memory_mib": 16384, "enabled": True}
    body = {"cpus": 3, "memory_mib": 16384, "enabled": True, "expected": original}
    assert web.post("/v1/web/admin/servers/A", headers=auth(), json=body).status_code == 409
    assert (
        web.post(
            "/v1/web/admin/servers/A", headers=auth(), json={**body, "cpus": 8, "enabled": False}
        ).status_code
        == 200
    )
    assert (
        web.post("/v1/web/admin/servers/A", headers=auth(), json={**body, "cpus": 8}).status_code
        == 409
    )
    assert (
        web.post(
            "/v1/web/admin/users/alice/grants",
            headers=auth(),
            json={"allowed_nodes": [], "expected_nodes": ["A"]},
        ).status_code
        == 200
    )
    assert (
        web.post(
            "/v1/web/admin/users/alice/grants",
            headers=auth(),
            json={"allowed_nodes": ["A"], "expected_nodes": ["A"]},
        ).status_code
        == 409
    )
    assert rig.state(job) == "DISPATCHING"
    assert web.get("/v1/web/servers", headers=auth()).json() == []
    assert web.get("/v1/web/admin/servers", headers=auth()).json()[0]["reserved_cpus"] == 4
    # Reporting completion remains possible after a server grant is revoked.
    rig.event(job)
    assert rig.state(job) == "SUCCEEDED"


def test_recent_job_pagination_redacts_commands_and_other_users(rig, web):
    ids = []
    for i in range(3):
        job = rig.submit(name=f"job-{i}", cpus=1, argv=["secret-argument"], workdir="/private/path")
        ids.append(job["id"])
    first = web.get("/v1/web/jobs?page_size=2", headers=auth())
    second = web.get("/v1/web/jobs?page_size=2&page=2", headers=auth())
    assert first.json()["total"] == 3
    assert [r["id"] for r in first.json()["items"] + second.json()["items"]] == list(reversed(ids))
    assert "secret-argument" not in first.text and "/private/path" not in first.text
    assert web.get("/v1/web/jobs?page_size=101", headers=auth()).status_code == 422


def test_completed_jobs_are_opt_in_and_do_not_consume_numbers_across_pages(rig, web):
    oldest = rig.submit(cpus=1, memory_mib=256)
    succeeded = rig.submit(cpus=1, memory_mib=256, argv=["private-finished-command"])
    rig.event(succeeded)
    failed = rig.submit(cpus=1, memory_mib=256)
    rig.event(failed, "failed", exit_code=1)
    running = rig.submit(cpus=1, memory_mib=256)
    rig.event(running, "started")
    cancelled = rig.submit()
    rig.hub.cancel("alice", cancelled["id"])
    waiting = rig.submit()
    other = rig.submit(user="bob", cpus=1, memory_mib=256)
    route = "/v1/web/jobs?page_size=2"
    first = web.get(route, headers=auth()).json()
    second = web.get(route + "&page=2", headers=auth()).json()
    active = first["items"] + second["items"]
    assert first["total"] == second["total"] == 3
    assert [j["id"] for j in active] == [running["id"], waiting["id"], oldest["id"]]
    assert [j["number"] for j in active] == [1, 2, 3]
    responses = [
        web.get(route + f"&include_completed=true&page={page}", headers=auth())
        for page in (1, 2, 3)
    ]
    all_jobs = [j for r in responses for j in r.json()["items"]]
    assert all(r.json()["total"] == 6 for r in responses)
    assert [j["id"] for j in all_jobs] == [
        running["id"],
        waiting["id"],
        oldest["id"],
        cancelled["id"],
        failed["id"],
        succeeded["id"],
    ]
    assert [j["number"] for j in all_jobs] == [1, 2, 3, None, None, None]
    assert other["id"] not in json.dumps(all_jobs)
    assert "private-finished-command" not in json.dumps(all_jobs)
    assert web.get(route + "&include_completed=false", headers=auth()).json()["total"] == 3
    assert web.get(route + "&include_completed=invalid", headers=auth()).status_code == 422


def test_last_completed_job_disappears_from_default_view_but_remains_in_history(rig, web):
    job = rig.submit()
    rig.event(job)
    default = web.get("/v1/web/jobs", headers=auth()).json()
    assert default["items"] == [] and default["total"] == 0
    history = web.get("/v1/web/jobs?include_completed=true", headers=auth()).json()
    assert history["total"] == 1 and history["items"][0]["id"] == job["id"]
    assert history["items"][0]["number"] is None


@pytest.mark.parametrize("node", [None, "A"])
@pytest.mark.parametrize("include_completed", [False, True])
def test_running_jobs_lead_all_pages_and_numbers_follow_display_order(
    rig, web, node, include_completed
):
    finished = rig.submit(cpus=1, memory_mib=256)
    rig.event(finished)
    first = rig.submit(cpus=1, memory_mib=256)
    rig.event(first, "started")
    second = rig.submit(cpus=1, memory_mib=256)
    rig.event(second, "started")
    preparing = rig.submit(cpus=6, memory_mib=256)
    waiting = rig.submit(cpus=1, memory_mib=256)
    route = "/v1/web/jobs"
    params = {"page_size": 1, "include_completed": str(include_completed).lower()}
    if node:
        params["node_id"] = node
    expected = [second["id"], first["id"], waiting["id"], preparing["id"]]
    if include_completed:
        expected.append(finished["id"])
    pages = [
        web.get(route, params={**params, "page": page}, headers=auth()).json()
        for page in range(1, len(expected) + 1)
    ]
    assert all(page["total"] == len(expected) for page in pages)
    items = [page["items"][0] for page in pages]
    assert [item["id"] for item in items] == expected
    assert [item["number"] for item in items] == [1, 2, 3, 4] + (
        [None] if include_completed else []
    )
    rig.event(second)
    refreshed = web.get(route, params=params, headers=auth()).json()["items"][0]
    assert refreshed["id"] == first["id"] and refreshed["number"] == 1


def test_own_jobs_filter_before_pagination_and_follow_assignment(rig, web):
    rig.hub.create_node(NodeCreate(id="B", cpus=8, memory_mib=16384))
    rig.hub.set_grants("alice", ["A", "B"])
    other = rig.hub.register_environment(
        "alice", EnvironmentCreate(name="other", node_id="B", ssh_target="alice@b", workdir="/work")
    )
    rig.hub.verify_environment(
        "B",
        other["id"],
        EnvironmentVerification(
            challenge=other["challenge"],
            container_id="alice-b",
            uid=1000,
            gid=1000,
            cpus=8,
            memory_mib=16384,
            gpu_ids=[],
        ),
    )
    same_node = rig.add_environment("alice", "another-alice-container")
    finished = rig.submit(cpus=1, memory_mib=256)
    rig.event(finished)
    blocker = rig.submit(cpus=8, memory_mib=256)
    private = rig.submit(user="bob", cpus=1, memory_mib=256)
    shared = rig.submit(
        environment_ids=[rig.env, same_node, other["id"]],
        cpus=1,
        memory_mib=256,
        argv=["private-command"],
    )
    a_only = rig.submit(cpus=1, memory_mib=256)
    b_only = rig.submit(environment_ids=[other["id"]], cpus=1, memory_mib=256)
    cancelled = rig.submit(environment_ids=[other["id"]], cpus=1, memory_mib=256)
    rig.hub.cancel("alice", cancelled["id"])
    assert rig.state(shared) == "QUEUED"

    route = "/v1/web/jobs?node_id=A&page_size=2"
    pages = [web.get(route + f"&page={page}", headers=auth()).json() for page in (1, 2)]
    jobs = [job for page in pages for job in page["items"]]
    assert all(page["total"] == 3 for page in pages)
    assert [j["id"] for j in jobs] == [a_only["id"], shared["id"], blocker["id"]]
    assert [j["number"] for j in jobs] == [1, 2, 3]
    assert private["id"] not in json.dumps(jobs) and "private-command" not in json.dumps(jobs)
    history = web.get(route + "&include_completed=true&page=2", headers=auth()).json()
    assert history["total"] == 4
    assert [j["id"] for j in history["items"]] == [blocker["id"], finished["id"]]
    assert [j["number"] for j in history["items"]] == [3, None]

    route_b = "/v1/web/jobs?node_id=B"
    b_jobs = web.get(route_b, headers=auth()).json()
    assert b_jobs["total"] == 2
    assert [j["id"] for j in b_jobs["items"]] == [b_only["id"], shared["id"]]
    assert [j["number"] for j in b_jobs["items"]] == [1, 2]
    b_history = web.get(route_b + "&include_completed=true", headers=auth()).json()
    assert [j["id"] for j in b_history["items"]] == [b_only["id"], shared["id"], cancelled["id"]]
    assert [j["number"] for j in b_history["items"]] == [1, 2, None]
    assert web.get("/v1/web/jobs", headers=auth()).json()["total"] == 4

    # Once assigned, a job belongs only to its actual server, including its history.
    rig.event(blocker)
    assigned = rig.hub.get_job("alice", shared["id"])
    assert assigned["node_id"] == "A"
    assert shared["id"] not in web.get(route_b, headers=auth()).text
    rig.event(assigned)
    assert shared["id"] not in web.get(route_b + "&include_completed=true", headers=auth()).text
    assert (
        web.get("/v1/web/jobs?node_id=A&include_completed=true", headers=auth()).json()["total"]
        == 4
    )


def test_own_jobs_server_filter_requires_current_grant_even_for_admin(rig, web):
    rig.hub.create_node(NodeCreate(id="PRIVATE", cpus=8, memory_mib=16384))
    job = rig.submit()
    for identity in ("google-alice", "google-bob"):
        for node in ("PRIVATE", "MISSING"):
            assert (
                web.get(
                    "/v1/web/jobs", params={"node_id": node}, headers=auth(identity)
                ).status_code
                == 403
            )
    for node in ("", "A' OR 1=1", "x" * 81):
        assert web.get("/v1/web/jobs", params={"node_id": node}, headers=auth()).status_code == 422
    rig.hub.set_grants("alice", [])
    assert web.get("/v1/web/jobs?node_id=A", headers=auth()).status_code == 403
    own = web.get("/v1/web/jobs", headers=auth()).json()["items"]
    assert len(own) == 1 and own[0]["id"] == job["id"] and own[0]["node_id"] is None
    assert rig.state(job) == "DISPATCHING"


def test_shared_server_jobs_show_reservations_and_redact_private_execution_details(rig, web):
    preparing = rig.submit(cpus=4, memory_mib=4096)
    running = rig.submit(
        user="bob",
        name="shared-analysis",
        cpus=4,
        memory_mib=4096,
        gpu_count=1,
        argv=["private-command", "private-argument"],
        workdir="/private/path",
    )
    rig.event(running, "started")
    queued = [rig.submit(name=f"waiting-{i}") for i in range(3)]
    route = "/v1/web/servers/A/jobs"
    response = web.get(route, headers=auth())
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert [r["id"] for r in data["items"]] == [running["id"], preparing["id"]]
    assert web.get(route + "?page_size=1", headers=auth()).json()["items"][0]["id"] == running["id"]
    shared = data["items"][0]
    assert shared["user_id"] == "bob" and shared["name"] == "shared-analysis"
    assert shared["state"] == "RUNNING" and shared["gpu_count"] == 1
    assert shared["started_at"] == rig.now
    assert set(shared) == {
        "id",
        "user_id",
        "name",
        "state",
        "reason",
        "cpus",
        "memory_mib",
        "gpu_count",
        "created_at",
        "started_at",
        "multiple_candidates",
    }
    for private in [
        "private-command",
        "private-argument",
        "/private/path",
        rig.bob_env,
        running["execution_id"],
        "request_key",
        "spec",
        "log_path",
    ]:
        assert private not in response.text
    first = web.get(route + "?group=queued&page_size=2", headers=auth()).json()
    second = web.get(route + "?group=queued&page_size=2&page=2", headers=auth()).json()
    assert first["total"] == 3 and second["total"] == 3
    assert [r["id"] for r in first["items"] + second["items"]] == [j["id"] for j in queued]
    assert all(j["node_id"] is None for j in queued)
    assert all(r["reason"] == "WAITING_FOR_RESOURCES_OR_ENVIRONMENT" for r in first["items"])
    counts = web.get("/v1/web/servers", headers=auth()).json()[0]["job_counts"]
    assert counts == {"RUNNING": 1, "DISPATCHING": 1, "UNKNOWN": 0, "QUEUED": 3}
    # Completing a job removes it from the shared reservation list.
    rig.event(running)
    assert running["id"] not in web.get(route, headers=auth()).text
    rig.now += 31
    rig.hub.tick()
    unknown = web.get(route, headers=auth()).json()
    assert unknown["total"] == 1 and unknown["items"][0]["state"] == "UNKNOWN"
    server = web.get("/v1/web/servers", headers=auth()).json()[0]
    assert server["reserved_cpus"] == 4 and server["job_counts"]["UNKNOWN"] == 1
    assert rig.state(preparing) == "UNKNOWN"


def test_shared_jobs_require_server_grant_even_for_web_admin(rig, web):
    rig.hub.create_node(NodeCreate(id="PRIVATE", cpus=8, memory_mib=16384))
    job = rig.submit(user="bob")
    route = "/v1/web/servers/A/jobs"
    for group in ["active", "queued"]:
        assert web.get(route + "?group=" + group).status_code == 401
        assert web.get(route + "?group=" + group, headers=auth(rig.alice)).status_code == 401
        for identity in ["google-alice", "google-bob"]:
            assert (
                web.get(
                    "/v1/web/servers/PRIVATE/jobs?group=" + group, headers=auth(identity)
                ).status_code
                == 403
            )
    for query in ["group=all", "page=0", "page_size=101"]:
        assert web.get(route + "?" + query, headers=auth()).status_code == 422
    rig.hub.set_grants("alice", [])
    assert web.get(route, headers=auth()).status_code == 403
    assert web.get(route, headers=auth("google-bob")).json()["total"] == 1
    assert web.get("/v1/web/servers", headers=auth()).json() == []
    assert rig.state(job, "bob") == "DISPATCHING"


def test_queued_server_candidates_are_deduplicated_without_revealing_other_servers(rig, web):
    # Alice can view A; Bob can submit to both A and PRIVATE.
    rig.hub.create_node(NodeCreate(id="PRIVATE", cpus=8, memory_mib=16384))
    rig.hub.set_grants("bob", ["A", "PRIVATE"])
    other = rig.hub.register_environment(
        "bob",
        EnvironmentCreate(
            name="other", node_id="PRIVATE", ssh_target="bob@private", workdir="/work"
        ),
    )
    rig.hub.verify_environment(
        "PRIVATE",
        other["id"],
        EnvironmentVerification(
            challenge=other["challenge"],
            container_id="private-container",
            uid=1001,
            gid=1000,
            cpus=8,
            memory_mib=16384,
            gpu_ids=[],
        ),
    )
    same_node = rig.add_environment("bob", "another-container")
    rig.submit()  # A is full; PRIVATE has no live heartbeat.
    shared = rig.submit(user="bob", environment_ids=[other["id"], rig.bob_env, same_node])
    private_only = rig.submit(user="bob", environment_ids=[other["id"]])
    same_node_only = rig.submit(user="bob", environment_ids=[rig.bob_env, same_node])
    route = "/v1/web/servers/A/jobs?group=queued"
    response = web.get(route, headers=auth())
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert [j["id"] for j in data["items"]] == [shared["id"], same_node_only["id"]]
    assert [j["multiple_candidates"] for j in data["items"]] == [True, False]
    assert "PRIVATE" not in response.text and other["id"] not in response.text
    assert private_only["id"] not in response.text
    assert web.get("/v1/web/servers", headers=auth()).json()[0]["job_counts"]["QUEUED"] == 2
    elsewhere = web.get(
        "/v1/web/servers/PRIVATE/jobs?group=queued", headers=auth("google-bob")
    ).json()
    assert [j["id"] for j in elsewhere["items"]] == [shared["id"], private_only["id"]]
    # Freeing A assigns the shared job only to A and removes it from every candidate queue.
    with rig.hub.store.transaction(write=False) as db:
        first_id = db.execute("SELECT id FROM jobs WHERE state='DISPATCHING'").fetchone()[0]
    rig.event(rig.hub.get_job("alice", first_id))
    assert rig.hub.get_job("bob", shared["id"])["node_id"] == "A"
    assert shared["id"] not in web.get(route, headers=auth()).text
    assert (
        shared["id"] not in web.get("/v1/web/servers/PRIVATE/jobs", headers=auth("google-bob")).text
    )
    assert (
        shared["id"]
        not in web.get("/v1/web/servers/PRIVATE/jobs?group=queued", headers=auth("google-bob")).text
    )


def test_google_account_creation_is_private_persistent_and_keeps_user_role(rig, web):
    body = {
        "id": "charlie",
        "firebase_uid": "google-charlie",
        "uid": 1002,
        "gid": 1000,
        "allowed_nodes": [],
    }
    assert web.post("/v1/web/admin/users", headers=auth("google-bob"), json=body).status_code == 403
    response = web.post("/v1/web/admin/users", headers=auth(), json=body)
    assert response.status_code == 201 and response.json()["created"]
    assert "token" not in response.text
    assert web.get("/v1/web/me", headers=auth("google-charlie")).json() == {"user_id": "charlie"}
    assert web.get("/v1/web/profile", headers=auth("google-charlie")).json()["allowed_nodes"] == []
    assert not web.get("/v1/web/profile", headers=auth("google-charlie")).json()["is_admin"]
    assert (
        web.post("/v1/web/admin/users", headers=auth(), json={**body, "id": "eve"}).status_code
        == 409
    )
    with rig.hub.store.transaction(write=False) as db:
        assert db.execute("SELECT role FROM principals WHERE id='charlie'").fetchone()[0] == "user"
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE principals SET enabled=0 WHERE id='charlie'")
    assert web.get("/v1/web/profile", headers=auth("google-charlie")).status_code == 403


def test_ssh_registration_and_approval_share_connector_and_identity_checks(rig, web):
    env = rig.hub.local.register(
        "alice",
        LocalEnvironment(
            instance_id="portal-main",
            node_id="A",
            name="main",
            ssh_target="local",
            workdir="/work",
            uid=1000,
            gid=1000,
            cpus=8,
            memory_mib=16000,
            gpu_ids=["GPU-1"],
        ),
    )
    connector = rig.hub.management.register_connector(
        "alice",
        type(
            "Body",
            (),
            {"instance_id": "connector-main", "environment_id": env["id"], "name": "main"},
        )(),
    )
    body = {
        "request_key": "ssh-test-request",
        "connector_id": connector["id"],
        "target": {
            "node_id": "A",
            "host": "server.test",
            "user": "alice",
            "port": 11019,
            "workdir": "/work",
        },
    }
    assert (
        web.post("/v1/web/registrations", headers=auth("google-bob"), json=body).status_code == 404
    )
    assert web.post("/v1/web/registrations", headers=auth(), json=body).json()["state"] == "PENDING"
    assert (
        web.post(
            "/v1/web/registrations", headers=auth(), json={**body, "argv": ["unsafe"]}
        ).status_code
        == 422
    )
    pending = web.get("/v1/web/admin/environments/pending", headers=auth()).json()
    assert pending["total"] == 0
    route = "/v1/web/admin/environments/" + env["id"] + "/approve"
    assert web.post(route, headers=auth("google-bob"), json={}).status_code == 403
    assert web.post(route, headers=auth(), json={}).json()["approved"]
    assert web.get("/v1/web/admin/environments/pending", headers=auth()).json()["total"] == 0
    assert (
        web.get("/v1/web/admin/audit", headers=auth()).json()["items"][0]["action"]
        == "environment.approve"
    )


@pytest.mark.sqlite_only
def test_schema_four_upgrade_preserves_existing_jobs_and_credentials(rig):
    job = rig.submit(cpus=1)
    with rig.hub.store.transaction() as db:
        for table in ("server_access_requests", "web_accounts", "web_audit"):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=4")
    store = rig.reopen_store()
    with store.transaction(write=False) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            db.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == "DISPATCHING"
        )
    assert rig.hub.authenticate(rig.alice)["id"] == "alice"


def test_postgres_schema_four_upgrade_keeps_reservations(rig):
    if rig.hub.store.backend != "postgresql":
        pytest.skip("PostgreSQL schema upgrade")
    job = rig.submit(cpus=1)
    with rig.hub.store.transaction() as db:
        for table in ("server_access_requests", "web_accounts", "web_audit"):
            db.execute("DROP TABLE " + table)
        db.execute("UPDATE hub_schema SET version=4 WHERE id=1")
    store = rig.reopen_store()
    with store.transaction(write=False) as db:
        assert (
            db.execute("SELECT version FROM hub_schema WHERE id=1").fetchone()[0] == SCHEMA_VERSION
        )
        assert (
            db.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == "DISPATCHING"
        )
    assert rig.hub.authenticate(rig.alice)["id"] == "alice"


def test_link_existing_user_preserves_mcp_token_and_grants(rig, web):
    from cowork_hub.models import UserCreate

    token = rig.hub.create_user(
        UserCreate(id="dana", allowed_nodes=["A"], identity={"uid": 1003, "gid": 1000})
    )["token"]
    body = {
        "id": "dana",
        "firebase_uid": "google-dana",
        "uid": 1003,
        "gid": 1000,
        "allowed_nodes": [],
    }
    response = web.post("/v1/web/admin/users", headers=auth(), json=body)
    assert response.status_code == 201 and not response.json()["created"]
    assert web.get("/v1/web/profile", headers=auth("google-dana")).json()["allowed_nodes"] == ["A"]
    assert rig.hub.authenticate(token)["id"] == "dana"
    assert (
        web.post(
            "/v1/web/admin/users", headers=auth(), json={**body, "firebase_uid": "google-other"}
        ).status_code
        == 409
    )
