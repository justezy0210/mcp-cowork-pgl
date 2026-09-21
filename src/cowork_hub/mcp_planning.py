"""Read-only decision support; every suggested change still requires user selection."""

import asyncio

from mcp.server.fastmcp.exceptions import ToolError


async def plan_with_choices(client, primary, alternatives):
    result = await client.request(
        "POST",
        "/v1/jobs/plan",
        body={
            "primary": primary.model_dump(exclude_none=True),
            "alternatives": [spec.model_dump(exclude_none=True) for spec in alternatives],
        },
    )
    profiles = result["profiles"]
    original = profiles[0]
    choices = []
    for index, profile in enumerate(profiles):
        if profile["can_start_now"]:
            choices.append(
                {
                    "kind": "run_original" if index == 0 else "run_alternative",
                    "profile_index": index,
                    "assignment": profile["assignment"],
                    "mode": "start_if_available",
                    "requires_user_choice": index != 0,
                }
            )
    if original["possible"] and not original["can_start_now"]:
        choices.insert(
            0,
            {
                "kind": "wait",
                "profile_index": 0,
                "mode": "queue_if_unavailable",
                "requires_user_choice": True,
            },
        )
    result["choices"] = choices
    result["alternatives_need_preparation"] = not original["can_start_now"] and not alternatives
    result["guidance"] = (
        "No resources were reserved. Explain the choices and obtain the user's selection before "
        "queuing, changing resources/argv, or choosing another environment, unless already chosen "
        "in the user's instructions. CPU alternatives must "
        "contain the corresponding real threads arguments. Do not reduce memory without evidence. "
        "Availability describes declared reservations, not measured utilization or unmanaged jobs."
    )
    if original["can_start_now"]:
        return result
    environments, nodes = await asyncio.gather(
        client.request("GET", "/v1/environments", params={"limit": 100}),
        client.request("GET", "/v1/cluster"),
    )
    primary_nodes = {e["node_id"] for e in environments if e["id"] in primary.environment_ids}
    ready = [e for e in environments if e["status"] == "READY"]
    result["node_availability"] = [
        {
            "node_id": node["id"],
            "available_cpus": max(0, node["cpus"] - node["reserved_cpus"]),
            "available_memory_mib": max(0, node["memory_mib"] - node["reserved_memory_mib"]),
            "unreserved_gpu_count": sum(not gpu["reserved"] for gpu in node["gpus"]),
        }
        for node in nodes
    ]
    result["setup_required_nodes"] = [
        node["id"] for node in nodes if node["id"] not in {e["node_id"] for e in ready}
    ]
    remote = [
        e
        for e in ready
        if e["node_id"] not in primary_nodes and e["id"] not in primary.environment_ids
    ]
    # Bound extra HTTP work and report explicitly when this is not an exhaustive scan.
    result["other_environment_scan_complete"] = len(environments) < 100 and len(remote) <= 8

    async def check(env):
        spec = primary.model_copy(update={"environment_ids": [env["id"]]})
        try:
            data = await client.request(
                "POST", "/v1/jobs/plan", body={"primary": spec.model_dump(exclude_none=True)}
            )
        except ToolError:
            return None, False
        if data["profiles"][0]["can_start_now"]:
            return {
                "kind": "use_other_environment",
                "environment_id": env["id"],
                "node_id": env["node_id"],
                "ssh_target": env["ssh_target"],
                "spec": spec.model_dump(),
                "mode": "start_if_available",
                "requires_user_choice": True,
                "requires_target_runner": True,
                "note": "Connect to this environment and use its runner; target path/program access is not checked here.",
            }, True
        return None, True

    for choice, checked in await asyncio.gather(*(check(env) for env in remote[:8])):
        if choice:
            choices.append(choice)
        if not checked:
            result["other_environment_scan_complete"] = False
    return result
