"""User-scoped MCP planning, local submission, status, and cancellation."""

import argparse
import asyncio
import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_planning import plan_with_choices
from .models import Identifier, JobSpec, JobSubmit
from .runner import RunnerError
from .ssh_runner import SSHRegistration, checked_config, register_ssh, submit

PageSize = Annotated[int, Field(ge=1, le=100)]
EnvironmentCursor = Annotated[str, Field(max_length=80)]
JobCursor = Annotated[int, Field(ge=0)]
Alternatives = Annotated[list[JobSpec], Field(max_length=8)]
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
HUB_ERROR_CODES = {
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "NOT_FOUND",
    "FORBIDDEN_NODE",
    "ENVIRONMENT_NOT_VERIFIED",
    "USER_IDENTITY_REQUIRED",
    "IDENTITY_MISMATCH",
    "ENVIRONMENT_NOT_APPROVED",
    "LOCAL_RUNNER_REQUIRED",
    "INVALID_REQUEST",
    "REQUEST_TOO_LARGE",
    "CAPACITY_UNAVAILABLE",
    "NOTIFICATION_NOT_CONFIGURED",
    "UNSATISFIABLE",
    "IDEMPOTENCY_CONFLICT",
}
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)


def runner_tool_error(exc):
    code = str(exc).split(":", 1)[0].split(" ", 1)[0]
    allowed = HUB_ERROR_CODES | {
        "RUNNER_CONFIG_MISMATCH",
        "LOCAL_ENVIRONMENT_REQUIRED",
        "WORKDIR_REQUIRED",
        "SUBMISSION_PENDING",
        "LOCAL_LAUNCH_FAILED",
        "HUB_UNREACHABLE",
        "SELECT_ONE_ENVIRONMENT",
        "SSH_RUNNER_NOT_CONFIGURED",
        "SSH_RESULT_UNCERTAIN",
        "SSH_RUNTIME_INVALID",
        "SSH_CONFIG_PERMISSIONS",
        "SSH_ROUTE_CONFLICT",
        "SSH_SHARED_PATH_REQUIRED",
        "SSH_WORKDIR_MISSING",
        "SSH_GPU_PROBE_FAILED",
        "SSH_GPU_NODE_MISMATCH",
        "SSH_RUNNER_ERROR",
    }
    if code not in allowed:
        code = "LOCAL_RUNNER_ERROR"
    return ToolError(
        f"{code}: Check the runner configuration, SSH connection and job records. "
        "Retry uncertain submissions only with the same request_key and unchanged request; "
        "do not bypass reservations."
    )


class HubClient:
    def __init__(self, hub_url: str, token_file: Path, *, transport=None):
        try:
            url = urlsplit(hub_url)
            valid = (
                url.scheme in ("http", "https")
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path in ("", "/")
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Hub URL must be an HTTP(S) origin without credentials or query")
        self.hub_url = hub_url.rstrip("/")
        self.token_file = token_file.expanduser()
        self.transport = transport

    def _token(self):
        # Read at call time: installing a token later does not require restarting MCP.
        try:
            fd = os.open(self.token_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            raise ToolError(
                "TOKEN_NOT_CONFIGURED: Prepare your private hub user token file."
            ) from None
        except OSError:
            raise ToolError("TOKEN_UNREADABLE: Check the configured user token file.") from None
        try:
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_uid != os.geteuid()
                ):
                    raise ToolError(
                        "TOKEN_PERMISSIONS: Use a private, user-owned file (mode 0600)."
                    )
                raw = stream.read(4097)
        except OSError:
            raise ToolError("TOKEN_UNREADABLE: Check the configured user token file.") from None
        if len(raw) > 4096 or not re.fullmatch(rb"[A-Za-z0-9_-]{20,4096}\n?", raw):
            raise ToolError("TOKEN_INVALID: Expected one hub user token in the private file.")
        return raw.strip().decode("ascii")

    async def request(self, method, path, *, authenticated=True, params=None, body=None):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token()}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5),
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    method, self.hub_url + path, headers=headers, params=params, json=body
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ToolError("HUB_RESPONSE_TOO_LARGE: Request a smaller page.")
        except httpx.HTTPError:
            # Do not expose exception text, request headers, or upstream response bodies.
            raise ToolError(
                "HUB_UNREACHABLE: Check the hub address and network connection."
            ) from None
        try:
            data = json.loads(content)
        except (ValueError, UnicodeError):
            data = None
        if not 200 <= response.status_code < 300:
            if 300 <= response.status_code < 400:
                raise ToolError("HUB_REDIRECT_REJECTED: Configure the final hub address directly.")
            code = "HUB_REQUEST_REJECTED"
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                candidate = data["error"].get("code")
                if isinstance(candidate, str) and candidate in HUB_ERROR_CODES:
                    code = candidate
            raise ToolError(f"{code}: Hub rejected the request (HTTP {response.status_code}).")
        if not isinstance(data, (dict, list)):
            raise ToolError("HUB_INVALID_RESPONSE: Expected a JSON object or list.")
        return data


def create_mcp(client: HubClient, runner_config: Path | None = None):
    server = FastMCP(
        "cowork",
        instructions=(
            "Access a laboratory resource reservation hub as the configured user. "
            "CPU and memory values describe declared reservations, not measured utilization. "
            "Memory is in MiB. Unverified/unapproved environments cannot run jobs. "
            "Approved local environments execute through cowork-run in the user's container, "
            "without Docker management or a node Worker. Node online status can be false when idle. "
            "Call plan_job before submission; it never reserves resources or starts work. "
            "If the primary request cannot start, explain wait, explicitly prepared resource "
            "alternatives, and ready environments on other allowed servers. Obtain the user's "
            "choice before queuing, reducing resources/threads or switching environments, "
            "unless that choice is already explicit in the user's instructions. "
            "Never invent a safe memory reduction. submit_job uses the configured local runner "
            "or starts a registered target runner over the user's SSH connection. "
            "It requires exactly one environment, an absolute workdir and a stable request_key. "
            "register_ssh_environment prepares a target over SSH using shared project/runtime/token paths. "
            "New environments require administrator approval of their physical server mapping. "
            "Use the SAME request_key and request body for retries, even after timeout or restart; "
            "never use a new key to recover an uncertain submission. Its result confirms acceptance, "
            "not completion. After accepted=true, report the job ID/log path and end the turn. "
            "Do not poll or sleep until the job starts or finishes unless the user explicitly "
            "requests live monitoring or lifecycle testing. Choosing to wait means queueing, "
            "not agent monitoring. The detached runner survives MCP disconnection and the hub "
            "sends Discord start/finish notifications. cancel_job requests "
            "cancellation; assigned jobs keep reservations until their runner confirms termination. "
            "The target needs a runner, not an agent or MCP server. The hub never opens SSH. "
            "This adapter has no administrator or Worker reporting tools. "
            "Do not bypass the hub by executing a requested job locally when a tool fails. "
            "Never request or include authentication tokens in tool arguments."
        ),
        log_level="WARNING",
    )

    @server.tool(annotations=READ_ONLY)
    async def hub_health() -> dict[str, Any]:
        """Check hub availability without a token; this does not verify user authentication."""
        data = await client.request("GET", "/healthz", authenticated=False)
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise ToolError("HUB_UNHEALTHY: Hub did not report status ok.")
        return {"status": "ok", "user_authentication_checked": False}

    @server.tool(annotations=READ_ONLY)
    async def cluster_status() -> dict[str, Any]:
        """List the user's permitted servers, Worker availability, budgets and reservations."""
        return {"nodes": await client.request("GET", "/v1/cluster")}

    @server.tool(annotations=READ_ONLY)
    async def list_environments(
        limit: PageSize = 50, after: EnvironmentCursor = ""
    ) -> dict[str, Any]:
        """List the user's environments. Continue with the last environment id as after."""
        return {
            "environments": await client.request(
                "GET", "/v1/environments", params={"limit": limit, "after": after}
            )
        }

    @server.tool(annotations=READ_ONLY)
    async def list_jobs(limit: PageSize = 50, after: JobCursor = 0) -> dict[str, Any]:
        """List the user's jobs. Continue with the last job seq as after."""
        return {
            "jobs": await client.request("GET", "/v1/jobs", params={"limit": limit, "after": after})
        }

    @server.tool(annotations=READ_ONLY)
    async def job_status(job_id: Identifier) -> dict[str, Any]:
        """Read a snapshot of one owned job when asked for status. Do not poll after acceptance."""
        return await client.request("GET", f"/v1/jobs/{job_id}")

    @server.tool(annotations=READ_ONLY)
    async def notification_status() -> dict[str, Any]:
        """Read the user's Discord configuration status. Does not send a message."""
        return await client.request("GET", "/v1/notifications")

    @server.tool(annotations=READ_ONLY)
    async def plan_job(
        primary: JobSpec, alternatives: Alternatives | None = None
    ) -> dict[str, Any]:
        """Check an explicit argv/resource request and optional alternatives without reserving.

        Use registered environment IDs. Do not change threads, memory, or GPU requests without
        user agreement. Each alternative must contain its actual argv and requested resources.
        A positive result is a snapshot, not a reservation or a guarantee of later availability.
        """
        result = await plan_with_choices(client, primary, alternatives or [])
        if runner_config is not None:
            try:
                config = checked_config(runner_config, client.hub_url, client.token_file)
            except (RunnerError, OSError, ValueError, KeyError):
                raise ToolError(
                    "RUNNER_CONFIG_MISMATCH: Check the private runner configuration."
                ) from None
            routes = config.get("ssh_runners", {})
            result["submission_environment_ids"] = [config["environment_id"], *routes]
            for choice in result["choices"]:
                if choice["kind"] == "use_other_environment":
                    configured = choice["environment_id"] in routes
                    choice["requires_target_runner"] = not configured
                    choice["submission_transport"] = "ssh" if configured else None
                    choice["note"] = (
                        "This MCP can launch the target runner over user SSH; verify target paths/programs before submission."
                        if configured
                        else "Register an SSH runner route before submitting from this MCP; target paths/programs are not checked."
                    )
        return result

    @server.tool(annotations=WRITE)
    async def register_ssh_environment(target: SSHRegistration) -> dict[str, Any]:
        """Prepare and register a container using the main container's existing user SSH authentication.

        Supply host, user, port, allowed node_id and absolute target workdir. Requires the same
        shared project/Python runtime/private user-token paths on both containers. No token values
        or administrator credential are accepted. The target does not need MCP or an agent.
        Repeating the same target is safe. A newly registered environment remains pending until
        an administrator confirms its physical node mapping; this tool never grants permission.
        """
        if runner_config is None:
            raise ToolError(
                "LOCAL_RUNNER_NOT_CONFIGURED: Configure --runner-config on this MCP server."
            )
        try:
            return await asyncio.to_thread(
                register_ssh,
                runner_config,
                target,
                hub_url=client.hub_url,
                token_file=client.token_file,
            )
        except RunnerError as exc:
            raise runner_tool_error(exc) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise ToolError(
                "SSH_RUNNER_ERROR: Check SSH access, shared paths and the private configuration."
            ) from None

    @server.tool(annotations=WRITE)
    async def submit_job(request: JobSubmit) -> dict[str, Any]:
        """Submit to a local or registered SSH runner without waiting for completion.

        Call plan_job first and obtain the user's selection when resources are unavailable.
        request.spec must select one registered runner environment and supply an
        absolute workdir. Keep argv/resources exactly as approved. start_if_available refuses
        to queue if capacity changed; use queue_if_unavailable only when waiting was chosen.
        Reuse the SAME request_key and body for every retry, including after reconnecting MCP.
        For a registered SSH environment, this MCP starts the detached target runner over SSH.
        SSH/MCP disconnects after acceptance do not stop the job. An uncertain SSH response must
        be retried with exactly the same key and request, never by executing the command directly.
        On accepted=true, report the job ID/log path and end the turn without waiting for start
        or completion. The runner waits and the hub sends Discord notifications. Monitor only
        if the user explicitly requests live monitoring or lifecycle testing.
        """
        if runner_config is None:
            raise ToolError(
                "LOCAL_RUNNER_NOT_CONFIGURED: Configure --runner-config on this MCP server."
            )
        try:
            result = await asyncio.to_thread(
                submit,
                runner_config,
                request,
                hub_url=client.hub_url,
                token_file=client.token_file,
            )
        except RunnerError as exc:
            raise runner_tool_error(exc) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise ToolError(
                "LOCAL_RUNNER_ERROR: Check the private runner configuration and job records."
            ) from None
        return {
            "accepted": True,
            "request_key": request.request_key,
            **result,
            "agent_action": "finish_turn",
            "guidance": (
                "Accepted, not necessarily started or completed. Report the job ID/log path and "
                "end this turn. The detached runner waits for resources, executes and reports; "
                "the hub sends Discord start/finish notifications. Do not poll status, sleep, "
                "watch logs or start an agent monitor unless the user explicitly requested live "
                "monitoring or lifecycle testing."
            ),
        }

    @server.tool(annotations=WRITE)
    async def cancel_job(job_id: Identifier) -> dict[str, Any]:
        """Request cancellation of one owned job; safe to repeat with the same job ID.

        A queued job can cancel immediately. For an assigned/running job this only requests
        cancellation: its runner must stop the process tree and report before resources return.
        Use job_status to confirm the final state; do not report a request as a completed stop.
        """
        return await client.request("POST", f"/v1/jobs/{job_id}/cancel")

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub-url", default=os.getenv("COWORK_HUB_URL"))
    parser.add_argument(
        "--token-file",
        type=Path,
        default=os.getenv("COWORK_TOKEN_FILE", str(Path.home() / ".config/cowork/user.token")),
    )
    parser.add_argument("--runner-config", type=Path, default=os.getenv("COWORK_RUNNER_CONFIG"))
    args = parser.parse_args()
    if not args.hub_url:
        parser.error("--hub-url or COWORK_HUB_URL is required")
    try:
        client = HubClient(args.hub_url, args.token_file)
    except ValueError as exc:
        parser.error(str(exc))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    create_mcp(client, args.runner_config).run(transport="stdio")


if __name__ == "__main__":
    main()
