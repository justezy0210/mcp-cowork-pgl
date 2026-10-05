#!/usr/bin/env python3
"""Exercise the actual stdio MCP protocol without running calculations or sending Discord."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOOLS = {
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


async def check(hub_url, token_file, *, health_only=False, runner_config=None):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cowork_hub.mcp_server", "--hub-url", hub_url, "--token-file", str(token_file)]
        + (["--runner-config", str(runner_config)] if runner_config else []),
    )
    async with asyncio.timeout(60):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                if names != TOOLS:
                    raise RuntimeError("Unexpected MCP tool catalog")
                print(json.dumps({"server": initialized.serverInfo.name, "tools": sorted(names)}))
                calls = ["hub_health"]
                if not health_only:
                    calls += [
                        "cluster_status",
                        "list_environments",
                        "list_jobs",
                        "notification_status",
                    ]
                for name in calls:
                    result = await session.call_tool(name, {})
                    if result.isError:
                        # The adapter already sanitizes errors. Only report a bounded error code.
                        message = next(
                            (item.text for item in result.content if item.type == "text"), ""
                        )
                        known = next(
                            (
                                code
                                for code in (
                                    "TOKEN_NOT_CONFIGURED",
                                    "TOKEN_UNREADABLE",
                                    "TOKEN_PERMISSIONS",
                                    "TOKEN_INVALID",
                                    "UNAUTHENTICATED",
                                    "FORBIDDEN",
                                    "HUB_UNREACHABLE",
                                )
                                if code in message
                            ),
                            "MCP_TOOL_ERROR",
                        )
                        print(json.dumps({"tool": name, "ok": False, "error": known}))
                        return False
                    data = result.structuredContent
                    if not isinstance(data, dict):
                        raise RuntimeError("MCP tool did not return structured output")
                    summary = {"tool": name, "ok": True}
                    if name == "cluster_status":
                        summary["nodes"] = [
                            {"id": node["id"], "online": node["online"]} for node in data["nodes"]
                        ]
                    elif name in ("list_environments", "list_jobs"):
                        summary["count"] = len(
                            data["environments" if name == "list_environments" else "jobs"]
                        )
                    print(json.dumps(summary))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub-url", default=os.getenv("COWORK_HUB_URL"))
    parser.add_argument(
        "--token-file",
        type=Path,
        default=os.getenv("COWORK_TOKEN_FILE", str(Path.home() / ".config/cowork/user.token")),
    )
    parser.add_argument("--runner-config", type=Path, default=os.getenv("COWORK_RUNNER_CONFIG"))
    parser.add_argument("--health-only", action="store_true", help="Skip authenticated calls")
    args = parser.parse_args()
    if not args.hub_url:
        parser.error("--hub-url or COWORK_HUB_URL is required")
    try:
        success = asyncio.run(
            check(
                args.hub_url,
                args.token_file,
                health_only=args.health_only,
                runner_config=args.runner_config,
            )
        )
    except Exception:
        print("MCP_CHECK_FAILED: Protocol/connection check failed.", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
