"""Passive first-use advice; canonical dispatch still authorizes every action."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def read_integration_use(service: Any, conversation_id: str, integration_id: str,
                         *, validate: Callable[[], None]) -> dict:
    from row_bot.application.client_integrations import read_integration
    from row_bot.application.workspace_setup import conversation_workspace
    from row_bot.agent_profiles import get_agent_profile
    from row_bot.agent_tool_catalog import list_cached_tools
    from row_bot.tools.profile_policy import dispatch_refusal
    from row_bot.application.capability_configuration_controls import _server_id

    validate()
    item = read_integration(integration_id, validate=validate)
    workspace = conversation_workspace(service, conversation_id)
    controls = workspace["controls"]
    reference = controls.get("profile_id", "")
    profile = get_agent_profile(reference, enabled_only=False) if reference else None
    result = {"conversation_id": conversation_id, "integration_id": integration_id,
              "conversation_revision": workspace["revision"], "eligible": False,
              "reason": "", "account_label": item["account_label"]}
    def answer(reason: str, eligible: bool = False) -> dict:
        validate()
        return {**result, "eligible": eligible, "reason": reason}
    if item["status"] != "ready":
        return answer("Finish this integration's setup or turn it on before trying it in chat.")
    if controls["runtime_mode"] == "chat_only":
        return answer("Choose Agent mode in this chat before using integrations.")
    model = workspace.get("model_status") or {}
    if model.get("state") != "ready":
        return answer("Choose an available tool-capable model in this chat. " + model.get("reason", ""))
    if reference and (not profile or not profile.get("enabled", True)):
        return answer("The selected profile is unavailable. Choose an enabled profile in this chat.")
    skill_ids = [item["owner_ref"]] if item["kind"] == "skill" else [child["owner_ref"] for child in item["children"] if child["kind"] == "skill" and child["status"] == "ready"]
    if skill_ids:
        selected = (profile.get("skill_policy_json") or {}).get("skills_override", []) if profile else skill_ids
        if any(identity in selected for identity in skill_ids):
            return answer("A draft can be prepared with this chat's current profile. Required tools and execution approvals are checked when you send.", True)
        if item["kind"] == "skill":
            return answer("This skill is not selected by the current profile. Add it in the profile library or explicitly choose a suitable profile in chat.")
    cursor = None
    candidates = []
    for _ in range(201):
        page = list_cached_tools(cursor=cursor, limit=50)
        for tool in page.items:
            matches = (tool.plugin_id == item["owner_ref"] if item["kind"] == "plugin" else
                       bool(tool.server_name and _server_id(tool.server_name) == item["owner_ref"]))
            if matches and tool.enabled is not False:
                if tool.source == "mcp" and tool.plugin_id:
                    from row_bot.application.capability_policy_controls import read_mcp_policy, _tool_id
                    child = next((child for child in item["children"] if child["kind"] == "mcp" and child["name"] == tool.server_name and child["status"] == "ready"), None)
                    if child is None:
                        continue
                    policy = read_mcp_policy(server_id=child["owner_ref"], target=child["target"], query=tool.label, limit=50, validate=validate)
                    if not any(row.tool_id == _tool_id(child["owner_ref"], tool.label) and row.enabled for row in policy.items):
                        continue
                elif tool.enabled is not True:
                    continue
                candidates.append(tool)
        cursor = page.next_cursor
        if not cursor:
            break
    for tool in candidates:
        source = ("plugin:" + tool.plugin_id) if tool.source in {"plugin", "custom"} else "mcp"
        if not dispatch_refusal(profile, tool.id, {}, source=source, parent=tool.parent_id or tool.id):
            return answer("A draft can be prepared for the accepted tools allowed by this profile. Runtime approvals still apply when you send.", True)
    if candidates:
        return answer("The current profile blocks these tools (tool scope or read-only policy). Review that profile or explicitly choose a suitable profile in chat.")
    return answer("No enabled tools from this integration are present in the local runtime catalog. Connect it and review tool access, then check again.")
