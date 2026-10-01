"""Settings › Tools › Custom tools: every custom tool, not only one code folder's.

Parity row 33: list, add from a folder (a desktop folder pick, never a path
from the page), switch on or off, make available in chat, Test and remove.
Parity row 34: a test command that needs approval is not run until the
person approves exactly that command once through the standard approval
card; the approval is bound to the tool, the command and its text.
Local owner on a direct loopback connection only (the route checks).
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
from pathlib import Path
import secrets
from typing import Any, Callable

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer import tool_capsules as capsules
from row_bot.developer.client_custom_tools import _draft_view, _tool_view
from row_bot.runtime import admissions

TARGET = "custom-tool:library"
_APPROVAL_KEY = secrets.token_bytes(32)
_ACTIONS = {"inspect", "refine", "update", "create", "setup", "test", "enable", "promote", "remove"}


def _folder(path: str) -> str:
    return Path(path or "").name[:256]


def read_custom_tool_library(*, validate: Callable[[], None]) -> dict:
    tools = capsules.list_capsules()
    drafts = capsules.list_custom_tool_drafts()
    created_by = {draft.created_tool_id: draft.id for draft in drafts if draft.created_tool_id}
    data: dict[str, Any] = {
        "schema_version": 1,
        "tools": [
            {**_tool_view(tool), "folder": _folder(tool.installed_path), "draft_id": created_by.get(tool.id, "")}
            for tool in tools
        ][:64],
        # Drafts that are not a tool yet (the builder continues them).
        "drafts": [
            {**_draft_view(draft), "folder": _folder(draft.installed_path)}
            for draft in drafts if not draft.created_tool_id
        ][:32],
    }
    data["revision"] = sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    validate()
    return data


# ------------------------------------------------------------- approvals

def _nonce(subject: str, command_name: str, command_text: str) -> str:
    message = json.dumps([subject, command_name, command_text], ensure_ascii=False).encode("utf-8")
    return hmac.new(_APPROVAL_KEY, message, sha256).hexdigest()


def approval_needed(subject: str, command_name: str, command_text: str,
                    approval_mode: Any) -> dict | None:
    """The approval card's content when this command needs the person's OK."""
    verdict = capsules.classify_custom_tool_command(command_text, approval_mode)
    if not verdict["requires_approval"] or verdict["blocked"]:
        return None
    reasons = {"run_network": "It uses the network.", "run_install": "It installs software.",
               "start_server": "It starts a server that keeps running.", "delete": "It can delete files.",
               "git_commit": "It changes the Git history.", "git_push": "It sends changes to a remote.",
               "git_pr": "It opens a pull request."}
    return {"command_name": command_name[:128], "command": command_text[:4096],
            "label": str(verdict["label"])[:64],
            "reason": reasons.get(str(verdict["action"]), "It needs your approval to run.")[:1024],
            "nonce": _nonce(subject, command_name, command_text)}


def approved(subject: str, command_name: str, command_text: str, nonce: Any) -> bool:
    return isinstance(nonce, str) and hmac.compare_digest(nonce, _nonce(subject, command_name, command_text))


def _test_view(result: Any) -> dict:
    return {"ran": bool(result.ran), "ok": bool(result.ok), "returncode": result.returncode,
            "stdout": str(result.stdout or "")[-4000:], "stderr": str(result.stderr or "")[-4000:],
            "setup_hint": ""}


# ------------------------------------------------------------- commands

def _clear_agent_cache() -> None:
    # A switched, removed or chat-available tool changes what the agent loads.
    try:
        from row_bot.agent import clear_agent_cache
        clear_agent_cache()
    except Exception:  # pragma: no cover - cache is rebuilt on the next turn anyway
        pass


def _tool(tool_id: str) -> capsules.ToolCapsule:
    tool = next((item for item in capsules.list_capsules() if item.id == tool_id), None)
    if tool is None:
        raise ClientPlatformError("custom_tool_unavailable")
    return tool


def _draft(draft_id: str) -> capsules.CustomToolDraft:
    try:
        return capsules.get_custom_tool_draft(draft_id)
    except KeyError:
        raise ClientPlatformError("custom_tool_draft_unavailable") from None


def _command(commands: list[dict], name: str) -> dict:
    requested = name.strip().lower()
    for item in commands:
        if str(item.get("name", "")).strip().lower() == requested:
            return item
    raise ClientPlatformError("invalid_custom_tool_command")


def _test(payload: dict, approval_mode: Any) -> tuple[str, str, dict | None, dict | None]:
    """Run one test command, or return the approval it needs first."""
    name = str(payload.get("command_name") or "")
    query = str(payload.get("query") or "")[:1024]
    if not name:
        raise ClientPlatformError("invalid_custom_tool_command")
    if payload.get("tool_id"):
        tool = _tool(str(payload["tool_id"]))
        subject, label = f"tool:{tool.id}", tool.name
        text = capsules.substitute_custom_tool_query(
            str(_command(tool.commands, name).get("command", "")), query,
            default_query=capsules.DEFAULT_CUSTOM_TOOL_TEST_QUERY)
    else:
        draft = _draft(str(payload.get("draft_id") or ""))
        subject, label = f"draft:{draft.id}", draft.name
        text = capsules.substitute_custom_tool_query(
            str(_command(draft.commands, name).get("command", "")), query,
            default_query=capsules.DEFAULT_CUSTOM_TOOL_TEST_QUERY)
    needed = approval_needed(subject, name, text, approval_mode)
    if needed and not approved(subject, name, text, payload.get("approval_nonce")):
        return "approval_required", f"{label}: this command needs your approval before it runs.", None, needed
    once = needed is not None
    if payload.get("tool_id"):
        result = capsules.run_custom_tool_test_command(
            str(payload["tool_id"]), text, approved_once=once, approval_mode=approval_mode)
    else:
        result = capsules.test_custom_tool_draft_command(
            str(payload["draft_id"]), command_name=name, approval_mode=approval_mode,
            query=query, approved_once=once)
    summary = ("Command passed." if result.ok else "Command failed." if result.ran
               else "The command was blocked by your approval setting.")
    return "completed", summary, _test_view(result), None


def execute_custom_tool_library(command: dict, *, owner_id: str, validate: Callable[[], None],
                                folder: Path | None = None, approval_mode: Any = None) -> dict:
    action = command["action"]
    payload = command.get("payload") or {}
    if action not in _ACTIONS:
        raise ClientPlatformError("invalid_custom_tool_command")
    mode = approval_mode or capsules._active_approval_mode()
    wire = {"command_id": command["command_id"], "type": f"custom-tool.{action}", "action": action,
            "revision": command["revision"], "payload": payload}
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != TARGET or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        return admissions.claim_command(owner_id, command["command_id"], wire, TARGET)
    snapshot = read_custom_tool_library(validate=validate)
    if command["revision"] != snapshot["revision"]:
        raise ClientPlatformError("custom_tool_revision_conflict")
    if action == "inspect" and folder is None:
        raise ClientPlatformError("invalid_custom_tool_command")
    draft_id, tool_id = str(payload.get("draft_id") or ""), str(payload.get("tool_id") or "")
    if action in {"refine", "update", "create", "setup"} and not draft_id:
        raise ClientPlatformError("invalid_custom_tool_command")
    if action in {"enable", "promote", "remove", "test"} and bool(draft_id) == bool(tool_id):
        raise ClientPlatformError("invalid_custom_tool_command")
    # What the command names must exist before it is admitted.
    if tool_id:
        _tool(tool_id)
    if draft_id:
        _draft(draft_id)
    prior = admissions.claim_command(owner_id, command["command_id"], wire, TARGET, exclusive_target=True)
    if prior is not None:
        return prior
    validate()
    try:
        status, summary, approval, test = _dispatch(action, payload, draft_id, tool_id, folder, mode)
    except (ValueError, KeyError, PermissionError, OSError):
        # The store refused (e.g. "create the tool before switching it on"):
        # a plain, finished failure; the list shows what is true now.
        status, summary, approval, test = ("failed", "That didn't work. Check the tool and try again.",
                                           None, None)
    validate()
    outcome = {"command_id": command["command_id"], "status": status, "summary": summary[:1024],
               "snapshot": read_custom_tool_library(validate=validate),
               "approval": approval, "test": test}
    return admissions.complete_command(owner_id, command["command_id"], outcome)


def _dispatch(action: str, payload: dict, draft_id: str, tool_id: str, folder: Path | None,
              mode: Any) -> tuple[str, str, dict | None, dict | None]:
    status, approval, test = "completed", None, None
    if action == "inspect":
        # Inspect sends bounded excerpts of the folder to the configured model;
        # the page says so before the person picks the folder.
        draft = capsules.create_custom_tool_draft(str(folder), source_url=str(folder), use_ai=True)
        summary = f"Inspected {draft.name}. Check its commands, then set it up."
    elif action == "refine":
        draft = capsules.refine_custom_tool_draft_with_llm(_draft(draft_id).id, str(payload.get("instruction") or ""))
        summary = f"Refined {draft.name}."
    elif action == "update":
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict) or set(fields) - {"name", "version", "commands"}:
            raise ClientPlatformError("invalid_custom_tool_command")
        summary = f"Saved {capsules.update_custom_tool_draft(_draft(draft_id).id, fields).name}."
    elif action == "create":
        summary = f"Created {capsules.create_tool_from_draft(_draft(draft_id).id, community=True).name}."
    elif action == "setup":
        result = capsules.setup_custom_tool_python_environment(_draft(draft_id).id)
        summary = str(result.get("message") or ("Python setup complete." if result.get("ok") else "Python setup failed."))[:1024]
    elif action == "test":
        status, summary, test, approval = _test(payload, mode)
    elif action == "enable":
        enabled = bool(payload.get("enabled", True))
        tool = (capsules.set_custom_tool_enabled(_tool(tool_id).id, enabled) if tool_id
                else capsules.enable_created_custom_tool_from_draft(_draft(draft_id).id, enabled))
        _clear_agent_cache()
        summary = f"{tool.name} is {'on' if tool.enabled else 'off'}."
    elif action == "promote":
        tool = (capsules.promote_custom_tool(_tool(tool_id).id) if tool_id
                else capsules.promote_created_custom_tool_from_draft(_draft(draft_id).id))
        _clear_agent_cache()
        summary = f"{tool.name} is available in chat."
    else:  # remove: the tool and its builder draft; the folder's files stay.
        if tool_id:
            name = _tool(tool_id).name
            capsules.remove_capsule(tool_id, delete_files=False)
            for draft in capsules.list_custom_tool_drafts():
                if draft.created_tool_id == tool_id:
                    capsules.delete_custom_tool_draft(draft.id)
        else:
            draft = _draft(draft_id)
            name = draft.name
            if draft.created_tool_id:
                capsules.remove_capsule(draft.created_tool_id, delete_files=False)
            capsules.delete_custom_tool_draft(draft.id)
        _clear_agent_cache()
        summary = f"Removed {name}. Its files stay in the folder."
    return status, summary, approval, test


def read_custom_tool_library_receipt(command_id: str, *, owner_id: str,
                                     validate: Callable[[], None]) -> dict:
    snapshot = read_custom_tool_library(validate=validate)
    meta = admissions.read_command_metadata(owner_id, command_id)
    if meta is None or meta["target"] != TARGET or not meta["type"].startswith("custom-tool."):
        raise ClientPlatformError("custom_tool_receipt_unavailable")
    receipt = admissions.read_command_receipt(owner_id, command_id)
    if receipt is None:
        raise ClientPlatformError("custom_tool_receipt_unavailable")
    if receipt["status"] == "admitting":
        return {"command_id": command_id, "status": "uncertain",
                "summary": "The previous action has an unconfirmed outcome. Check the list before trying again.",
                "snapshot": snapshot, "approval": None, "test": None}
    return receipt


