"""Bound, local-owner Custom Tool Builder actions for the React client.

The existing capsule store remains the only owner of drafts and tools.  This
facade never accepts a host path from the browser: the current workspace
binding supplies the source folder for inspection and every later action.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.workspace_process_commands import _scope
from row_bot.developer import tool_capsules as capsules
from row_bot.runtime import admissions


def _safe_commands(commands: list[dict]) -> list[dict]:
    return [
        {
            "name": str(item.get("name", ""))[:128],
            "description": str(item.get("description", ""))[:1024],
            "command": str(item.get("command", ""))[:4096],
        }
        for item in commands[:32]
        if isinstance(item, dict)
    ]


def _draft_view(draft: capsules.CustomToolDraft) -> dict:
    return {
        "id": draft.id,
        "name": draft.name[:256],
        "version": draft.version[:128],
        "commands": _safe_commands(draft.commands),
        "warnings": [str(value)[:1024] for value in draft.warnings[:32]],
        "test_results": {
            str(key)[:128]: {
                "ran": bool(value.get("ran")),
                "ok": bool(value.get("ok")),
                "returncode": value.get("returncode"),
                "stdout": str(value.get("stdout", ""))[-4000:],
                "stderr": str(value.get("stderr", ""))[-4000:],
                "setup_hint": str(value.get("setup_hint", ""))[:1024],
            }
            for key, value in list(draft.test_results.items())[:32]
            if isinstance(value, dict)
        },
        "python_project": bool(draft.environment.get("python_project")),
        "setup_ok": bool(draft.environment.get("setup_ok")),
        "status": draft.status[:64],
        "created_tool_id": draft.created_tool_id[:128],
    }


def _tool_view(tool: capsules.ToolCapsule) -> dict:
    return {
        "id": tool.id,
        "name": tool.name[:256],
        "version": tool.version[:128],
        "enabled": bool(tool.enabled),
        "available_in_chat": bool(tool.promoted_plugin_id),
        "commands": _safe_commands(tool.commands),
    }


def _bound(resource_id: str, conversation_id: str, validate: Callable[[], None]):
    workspace, binding, _revision, approval_mode = _scope(
        resource_id, conversation_id, validate
    )
    root = Path(workspace.path).resolve(strict=True)
    if not root.is_dir():
        raise ClientPlatformError("resource_unavailable")
    return workspace, binding, root, approval_mode


def read_custom_tools(
    resource_id: str, conversation_id: str, *, validate: Callable[[], None]
) -> dict:
    workspace, binding, root, _approval_mode = _bound(
        resource_id, conversation_id, validate
    )
    drafts = [
        _draft_view(draft)
        for draft in capsules.list_custom_tool_drafts()
        if Path(draft.installed_path).resolve() == root
    ][:32]
    tools = [
        _tool_view(tool)
        for tool in capsules.list_capsules()
        if Path(tool.installed_path).resolve() == root
    ][:32]
    data = {
        "schema_version": 1,
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "binding_id": binding.binding_id,
        "workspace_name": root.name[:256],
        "source_is_repository": bool(workspace.repo_url),
        "drafts": drafts,
        "tools": tools,
    }
    data["revision"] = sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    validate()
    return data


def _draft_for_root(draft_id: str, root: Path) -> capsules.CustomToolDraft:
    try:
        draft = capsules.get_custom_tool_draft(draft_id)
    except KeyError:
        raise ClientPlatformError("custom_tool_draft_unavailable") from None
    if Path(draft.installed_path).resolve() != root:
        raise ClientPlatformError("custom_tool_draft_unavailable")
    return draft


def execute_custom_tool(
    resource_id: str,
    conversation_id: str,
    command: dict,
    *,
    owner_id: str,
    validate: Callable[[], None],
) -> dict:
    action = command["action"]
    payload = command.get("payload") or {}
    if action not in {"inspect", "refine", "update", "create", "setup", "test", "enable", "promote", "remove"}:
        raise ClientPlatformError("invalid_custom_tool_command")
    target = f"custom-tool:{resource_id}"
    wire = {
        "command_id": command["command_id"],
        "type": f"custom-tool.{action}",
        "action": action,
        "revision": command["revision"],
        "payload": payload,
    }
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != target or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        return admissions.claim_command(
            owner_id, command["command_id"], wire, target
        )
    snapshot = read_custom_tools(resource_id, conversation_id, validate=validate)
    if command["revision"] != snapshot["revision"]:
        raise ClientPlatformError("custom_tool_revision_conflict")
    workspace, _binding, root, approval_mode = _bound(
        resource_id, conversation_id, validate
    )
    draft_id = str(payload.get("draft_id") or "")
    if action != "inspect":
        _draft_for_root(draft_id, root)
    if action == "update":
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict) or set(fields) - {"name", "version", "commands"}:
            raise ClientPlatformError("invalid_custom_tool_command")
    if action == "test" and not str(payload.get("command_name") or ""):
        raise ClientPlatformError("invalid_custom_tool_command")
    if action == "refine" and not str(payload.get("instruction") or "").strip():
        raise ClientPlatformError("invalid_custom_tool_command")
    prior = admissions.claim_command(
        owner_id, command["command_id"], wire, target, exclusive_target=True
    )
    if prior is not None:
        return prior
    # A claimed command is never silently rerun after a lost response.  Any
    # unconfirmed result remains visible through the durable receipt endpoint.
    validate()
    if action == "inspect":
        try:
            draft = capsules.create_custom_tool_draft(
                str(root), source_url=workspace.repo_url or str(root), use_ai=True
            )
        except Exception:
            # A failed proposal can be retried once the current draft store
            # proves that this command saved nothing. An ambiguous write keeps
            # its original admission for receipt inspection instead.
            after = read_custom_tools(resource_id, conversation_id, validate=validate)
            if after["revision"] != snapshot["revision"]:
                raise
            return admissions.complete_command(
                owner_id,
                command["command_id"],
                {
                    "command_id": command["command_id"],
                    "status": "failed",
                    "summary": "Inspection failed before a draft was saved. Try again.",
                    "snapshot": after,
                },
            )
        summary = f"Inspected {draft.name}."
    elif action == "refine":
        draft = capsules.refine_custom_tool_draft_with_llm(
            draft_id, str(payload["instruction"])
        )
        summary = f"Refined {draft.name}."
    elif action == "update":
        draft = capsules.update_custom_tool_draft(draft_id, payload["fields"])
        summary = f"Saved {draft.name}."
    elif action == "create":
        tool = capsules.create_tool_from_draft(draft_id, community=True)
        summary = f"Created {tool.name}."
    elif action == "setup":
        result = capsules.setup_custom_tool_python_environment(draft_id)
        summary = str(result.get("message") or ("Python setup complete." if result.get("ok") else "Python setup failed."))[:1024]
    elif action == "test":
        result = capsules.test_custom_tool_draft_command(
            draft_id,
            command_name=str(payload["command_name"]),
            approval_mode=approval_mode,
            query=str(payload.get("query") or "")[:1024],
        )
        summary = "Command passed." if result.ok else ("Command failed." if result.ran else "Command requires approval or was blocked.")
    elif action == "enable":
        tool = capsules.enable_created_custom_tool_from_draft(
            draft_id, bool(payload.get("enabled", True))
        )
        summary = f"{tool.name} {'enabled' if tool.enabled else 'disabled'}."
    elif action == "promote":
        tool = capsules.promote_created_custom_tool_from_draft(draft_id)
        summary = f"{tool.name} is available in chat."
    elif action == "remove":
        draft = _draft_for_root(draft_id, root)
        if draft.created_tool_id:
            capsules.remove_capsule(draft.created_tool_id, delete_files=False)
        capsules.delete_custom_tool_draft(draft_id)
        summary = "Custom Tool removed; source files were preserved."
    else:
        raise ClientPlatformError("invalid_custom_tool_command")
    validate()
    outcome = {
        "command_id": command["command_id"],
        "status": "completed",
        "summary": summary,
        "snapshot": read_custom_tools(resource_id, conversation_id, validate=validate),
    }
    return admissions.complete_command(owner_id, command["command_id"], outcome)


def read_custom_tool_receipt(
    resource_id: str,
    conversation_id: str,
    command_id: str,
    *,
    owner_id: str,
    validate: Callable[[], None],
) -> dict:
    read_custom_tools(resource_id, conversation_id, validate=validate)
    meta = admissions.read_command_metadata(owner_id, command_id)
    if meta is None or meta["target"] != f"custom-tool:{resource_id}" or not meta["type"].startswith("custom-tool."):
        raise ClientPlatformError("custom_tool_receipt_unavailable")
    receipt = admissions.read_command_receipt(owner_id, command_id)
    if receipt is None:
        raise ClientPlatformError("custom_tool_receipt_unavailable")
    if receipt["status"] == "admitting":
        return {
            "command_id": command_id,
            "status": "uncertain",
            "summary": "The previous action has an unconfirmed outcome. Inspect the current draft before trying another action.",
            "snapshot": read_custom_tools(resource_id, conversation_id, validate=validate),
        }
    return receipt
