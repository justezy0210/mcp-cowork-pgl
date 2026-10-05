import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from test_web import settings, verify

pytest.importorskip("mcp", reason="Install the mcp extra to test the adapter")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from cowork_hub.api import create_app  # noqa: E402
from cowork_hub.mcp_server import HubClient, create_mcp  # noqa: E402


def token_file(path, token):
    path.write_text(token + "\n")
    path.chmod(0o600)
    return path


def test_catalog_write_tools_over_real_stdio(rig, tmp_path, live_hub):
    path = token_file(tmp_path / "catalog.token", rig.alice)

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "cowork_hub.mcp_server", "--hub-url", live_hub, "--token-file", str(path),
        ])
        async with asyncio.timeout(25):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(name, args=None):
                        result = await session.call_tool(name, args or {})
                        assert not result.isError, result
                        assert rig.alice not in result.model_dump_json()
                        return result.structuredContent

                    overview = await call("catalog_overview")
                    body = {"expected_revision": overview["revision"], "request_key": "stdio-registration-01",
                            "create_missing": True, "files": [{"project": "Test", "species": "Oryza sativa",
                            "sample": "Rice", "data_type": "ONT", "path": "/old/rice.fastq.gz", "bytes": 100, "node_id": "A"}]}
                    preview = await call("catalog_register_files", {"request": body})
                    assert not preview["applied"]
                    assert (await call("catalog_files"))["total"] == 0
                    result = await call("catalog_register_files", {"request": {**body, "dry_run": False}})
                    fid = result["changes"][0]["file_id"]
                    assert (await call("catalog_files", {"q": "rice"}))["total"] == 1
                    relocation = {"request_key": "stdio-relocation-01", "expected_revision": result["revision"],
                                  "dry_run": False, "files": [{"file_id": fid, "expected_path": "/old/rice.fastq.gz",
                                  "new_path": "/new/rice.fastq.gz", "node_id": "A", "observed_bytes": 100, "content_unchanged": True}]}
                    assert (await call("catalog_relocate_files", {"request": relocation}))["applied"]
                    assert (await call("catalog_file", {"file_id": fid}))["file"]["path"] == "/new/rice.fastq.gz"
                    assert len((await call("catalog_history", {"file_id": fid}))["items"]) == 2

    asyncio.run(scenario())


@pytest.fixture
def live_hub(rig):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(rig.hub, background=False, web_config=settings(admin_users=["alice"], catalog_access="approved"), web_verify=verify),
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Test hub did not start"
        yield address
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "Test hub did not stop"


def test_real_stdio_protocol_and_scoped_readonly_flow(rig, tmp_path, live_hub):
    path = token_file(tmp_path / "user.token", rig.alice)
    own_job = rig.submit(cpus=4)
    other_job = rig.submit(user="bob", cpus=4)
    before = rig.hub.list_jobs("alice")

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cowork_hub.mcp_server", "--hub-url", live_hub, "--token-file", str(path)],
        )
        async with asyncio.timeout(25):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    result = await session.initialize()
                    assert result.serverInfo.name == "cowork"
                    catalog = await session.list_tools()
                    assert {tool.name for tool in catalog.tools} == {
                        "hub_health",
                        "cluster_status",
                        "list_environments",
                        "list_jobs",
                        "job_status",
                        "notification_status",
                        "plan_job",
                        "submit_job",
                        "cancel_job",
                        "register_ssh_environment",
                        "catalog_overview",
                        "catalog_files",
                        "catalog_file",
                        "catalog_history",
                        "catalog_register_files",
                        "catalog_relocate_files",
                    }
                    assert all(
                        tool.annotations.readOnlyHint
                        == (
                            tool.name
                            not in {"submit_job", "cancel_job", "register_ssh_environment", "catalog_register_files", "catalog_relocate_files"}
                        )
                        for tool in catalog.tools
                    )
                    assert all(tool.annotations.idempotentHint for tool in catalog.tools)

                    async def call(name, args=None):
                        result = await session.call_tool(name, args or {})
                        assert not result.isError, result
                        assert rig.alice not in result.model_dump_json()
                        return result.structuredContent

                    assert (await call("hub_health"))["user_authentication_checked"] is False
                    assert [node["id"] for node in (await call("cluster_status"))["nodes"]] == ["A"]
                    environments = (await call("list_environments"))["environments"]
                    assert [env["id"] for env in environments] == [rig.env]
                    jobs = (await call("list_jobs"))["jobs"]
                    assert [job["id"] for job in jobs] == [own_job["id"]]
                    assert (await call("list_jobs", {"after": jobs[-1]["seq"]}))["jobs"] == []
                    assert (await call("notification_status"))["configured"] is True
                    assert (await call("job_status", {"job_id": own_job["id"]}))["id"] == own_job[
                        "id"
                    ]
                    plan = await call("plan_job", {"primary": rig.spec().model_dump()})
                    assert plan["reservation_created"] is False
                    assert plan["profiles"][0]["can_start_now"] is False
                    denied = await session.call_tool("job_status", {"job_id": other_job["id"]})
                    assert denied.isError
                    assert "NOT_FOUND" in denied.model_dump_json()
                    invalid = await session.call_tool("list_jobs", {"limit": 101})
                    assert invalid.isError
                    traversal = await session.call_tool("job_status", {"job_id": "../admin/users"})
                    assert traversal.isError
                    # A Worker credential must not become a user merely by loading it in MCP.
                    token_file(path, rig.worker)
                    wrong_role = await session.call_tool("cluster_status", {})
                    assert wrong_role.isError
                    assert "FORBIDDEN" in wrong_role.model_dump_json()

    asyncio.run(scenario())
    assert rig.hub.list_jobs("alice") == before


def test_check_command_uses_stdio_and_handles_missing_token(rig, tmp_path, live_hub):
    root = Path(__file__).resolve().parents[1]
    path = token_file(tmp_path / "user.token", rig.alice)
    command = [
        sys.executable,
        str(root / "scripts/check_mcp.py"),
        "--hub-url",
        live_hub,
        "--token-file",
        str(path),
    ]
    complete = subprocess.run(command, capture_output=True, text=True, timeout=25)
    assert complete.returncode == 0, complete.stdout + complete.stderr
    lines = [json.loads(line) for line in complete.stdout.splitlines()]
    assert len(lines) == 6
    assert lines[2]["nodes"] == [{"id": "A", "online": True}]
    assert rig.alice not in complete.stdout + complete.stderr
    path.unlink()
    missing = subprocess.run(command, capture_output=True, text=True, timeout=25)
    assert missing.returncode == 1
    assert "TOKEN_NOT_CONFIGURED" in missing.stdout
    health = subprocess.run(command + ["--health-only"], capture_output=True, text=True, timeout=25)
    assert health.returncode == 0
    assert 'cluster_status", "ok"' not in health.stdout


def test_private_token_handling_and_token_free_health(tmp_path):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    path = tmp_path / "user.token"
    client = HubClient("http://hub.test", path, transport=httpx.MockTransport(respond))
    asyncio.run(client.request("GET", "/healthz", authenticated=False))
    assert "authorization" not in seen[0].headers
    with pytest.raises(ToolError, match="TOKEN_NOT_CONFIGURED"):
        asyncio.run(client.request("GET", "/v1/cluster"))
    assert len(seen) == 1
    secret = "private-test-token-1234567890"
    token_file(path, secret)
    path.chmod(0o644)
    with pytest.raises(ToolError, match="TOKEN_PERMISSIONS"):
        asyncio.run(client.request("GET", "/v1/cluster"))
    path.chmod(0o600)
    asyncio.run(client.request("GET", "/v1/cluster"))
    assert seen[-1].headers["authorization"] == f"Bearer {secret}"
    link = tmp_path / "link.token"
    link.symlink_to(path)
    with pytest.raises(ToolError, match="TOKEN_UNREADABLE"):
        HubClient("http://hub.test", link)._token()


@pytest.mark.parametrize("kind", ["redirect", "error_body", "network"])
def test_errors_never_echo_upstream_secrets_or_follow_redirects(tmp_path, kind):
    secret = "PRIVATE_TEST_TOKEN_1234567890"
    path = token_file(tmp_path / "user.token", secret)
    calls = []

    def respond(request):
        calls.append(request)
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": f"https://other.test/{secret}"})
        if kind == "network":
            raise httpx.ConnectError(secret, request=request)
        return httpx.Response(500, json={"error": {"code": secret, "message": secret}})

    client = HubClient("http://hub.test", path, transport=httpx.MockTransport(respond))
    with pytest.raises(ToolError) as caught:
        asyncio.run(client.request("GET", "/v1/cluster"))
    assert secret not in str(caught.value)
    assert len(calls) == 1


def test_invalid_input_cannot_change_url_or_send_http(tmp_path):
    def unexpected(request):
        pytest.fail("Invalid MCP input reached HTTP")

    path = token_file(tmp_path / "user.token", "private-test-token-1234567890")
    server = create_mcp(
        HubClient("http://hub.test", path, transport=httpx.MockTransport(unexpected))
    )
    for name, args in [
        ("list_jobs", {"limit": 0}),
        ("list_environments", {"after": "x" * 81}),
        ("job_status", {"job_id": "../../admin/nodes"}),
        ("plan_job", {"primary": {"argv": ["echo", "test"]}}),
    ]:
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool(name, args))
    for url in ("file:///etc/passwd", "https://user:secret@hub.test", "https://hub.test?token=x"):
        with pytest.raises(ValueError):
            HubClient(url, path)


def test_cancel_tool_enforces_ownership_and_waits_for_execution_confirmation(
    rig, tmp_path, live_hub
):
    path = token_file(tmp_path / "user.token", rig.alice)
    own = rig.submit(cpus=4)
    other = rig.submit(user="bob", cpus=4)
    server = create_mcp(HubClient(live_hub, path))
    with pytest.raises(ToolError, match="NOT_FOUND"):
        asyncio.run(server.call_tool("cancel_job", {"job_id": other["id"]}))
    assert not rig.hub.get_job("bob", other["id"])["cancel_requested"]
    asyncio.run(server.call_tool("cancel_job", {"job_id": own["id"]}))
    asyncio.run(server.call_tool("cancel_job", {"job_id": own["id"]}))
    assert rig.hub.get_job("alice", own["id"])["cancel_requested"]
    assert rig.state(own) == "DISPATCHING"
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 8
    rig.event(own, "not_started")
    assert rig.state(own) == "CANCELLED"
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 4


def test_submit_requires_local_config_and_matching_mcp_credentials(tmp_path, monkeypatch):
    import os

    from cowork_hub.runner import save

    token = token_file(tmp_path / "user.token", "private-test-token-1234567890")
    client = HubClient("http://hub.test", token)
    body = {
        "request_key": "request-once",
        "spec": {
            "name": "test",
            "environment_ids": ["local-env"],
            "argv": ["true"],
            "cpus": 1,
            "memory_mib": 64,
            "workdir": str(tmp_path),
        },
    }
    with pytest.raises(ToolError, match="LOCAL_RUNNER_NOT_CONFIGURED"):
        asyncio.run(create_mcp(client).call_tool("submit_job", {"request": body}))
    config = tmp_path / "runner.json"
    save(
        config,
        {
            "hub_url": "http://other-hub.test",
            "token_file": str(token),
            "hostname": os.uname().nodename,
            "environment_id": "local-env",
            "environment": {"uid": os.getuid(), "gid": os.getgid()},
        },
    )

    def no_http(*args, **kwargs):
        pytest.fail("Mismatched runner config made an HTTP request")

    monkeypatch.setattr("cowork_hub.runner.Client", no_http)
    with pytest.raises(ToolError, match="RUNNER_CONFIG_MISMATCH"):
        asyncio.run(create_mcp(client, config).call_tool("submit_job", {"request": body}))
    value = json.loads(config.read_text())
    value["hub_url"] = "http://hub.test"
    value["token_file"] = str(tmp_path / "someone-else.token")
    save(config, value)
    with pytest.raises(ToolError, match="RUNNER_CONFIG_MISMATCH"):
        asyncio.run(create_mcp(client, config).call_tool("submit_job", {"request": body}))


def test_plan_offers_wait_explicit_alternative_and_ready_other_server(rig, tmp_path, live_hub):
    from cowork_hub.mcp_planning import plan_with_choices
    from cowork_hub.models import EnvironmentCreate, EnvironmentVerification, Heartbeat, NodeCreate

    for node in ("B", "C"):
        rig.hub.create_node(NodeCreate(id=node, cpus=8, memory_mib=16384))
    rig.hub.set_grants("alice", ["A", "B", "C"])
    remote = rig.hub.register_environment(
        "alice",
        EnvironmentCreate(name="alice-B", node_id="B", ssh_target="alice@B", workdir="/work"),
    )
    rig.hub.verify_environment(
        "B",
        remote["id"],
        EnvironmentVerification(
            challenge=remote["challenge"],
            container_id="alice-B",
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
                {"environment_id": remote["id"], "container_id": "alice-B", "ready": True}
            ]
        ),
    )
    rig.submit(cpus=4)
    primary = rig.spec()
    reduced = rig.spec(cpus=4, argv=["python", "analysis.py", "--threads", "4"])
    client = HubClient(live_hub, token_file(tmp_path / "user.token", rig.alice))
    before = rig.hub.list_jobs("alice")
    result = asyncio.run(plan_with_choices(client, primary, [reduced]))
    assert [x["kind"] for x in result["choices"]] == [
        "wait",
        "run_alternative",
        "use_other_environment",
    ]
    alternate = result["choices"][1]
    assert alternate["profile_index"] == 1 and alternate["requires_user_choice"]
    assert result["profiles"][1]["spec"]["argv"][-1] == "4"
    elsewhere = result["choices"][2]
    assert elsewhere["environment_id"] == remote["id"] and elsewhere["node_id"] == "B"
    assert elsewhere["requires_target_runner"] and elsewhere["requires_user_choice"]
    assert elsewhere["spec"]["argv"] == primary.argv
    assert elsewhere["spec"]["memory_mib"] == primary.memory_mib
    assert result["setup_required_nodes"] == ["C"]
    assert result["reservation_created"] is False and result["other_environment_scan_complete"]
    assert rig.hub.list_jobs("alice") == before
    without_alternatives = asyncio.run(plan_with_choices(client, primary, []))
    assert without_alternatives["alternatives_need_preparation"]
    assert not any(x["kind"] == "run_alternative" for x in without_alternatives["choices"])
