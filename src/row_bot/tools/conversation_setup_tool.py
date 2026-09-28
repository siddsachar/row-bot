"""Designs and code folders the assistant creates when the work needs one.

``create_design`` and ``create_code_folder`` bind a new resource to the current
conversation through the same ``resource.setup`` owner as Add resource. They
need no approval: they are local and the card in the chat offers Undo. The
work continues in a follow-up turn once this reply ends, when the new resource
is bound for the turn (a turn captures its bindings when it starts).

``create_code_folder`` with Developer tools off asks to turn them on through
the usual approval (shown as a "Turn on Developer tools" card) instead of
letting files land loosely in the workspace. ``request_connection`` shows a
"Connect …" card for an account or channel the work needs.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)

DesignType = Literal["deck", "document", "landing", "app_mockup", "storyboard"]
_DESIGN_WORDS = {
    "deck": "deck",
    "document": "document",
    "landing": "landing page",
    "app_mockup": "app mockup",
    "storyboard": "storyboard",
}
Connection = Literal[
    "google", "github", "x", "telegram", "slack", "discord", "sms", "whatsapp", "email",
]
_CONNECTIONS: dict[str, tuple[str, str]] = {
    "google": ("Google", "accounts"),
    "github": ("GitHub", "accounts"),
    "x": ("X", "accounts"),
    "telegram": ("Telegram", "channels"),
    "slack": ("Slack", "channels"),
    "discord": ("Discord", "channels"),
    "sms": ("SMS", "channels"),
    "whatsapp": ("WhatsApp", "channels"),
    "email": ("Email", "channels"),
}

GUIDANCE = (
    "CREATING A DESIGN OR CODE FOLDER: when the person asks you to build, make or write an app, "
    "website, script, program or other code project, call create_code_folder first (name it from "
    "the request); when they ask for a deck, slides, a presentation, a document to design, a landing "
    "page, a mockup or a storyboard, call create_design. Do not create one for questions, "
    "explanations, a short snippet in the chat, an image, a workflow, or when they want the result "
    "in the chat or the app only. If the conversation already has a code folder or the design they "
    "mean, keep working in it. After creating one, end your reply with one short sentence: the work "
    "continues by itself in the next step, where the new folder or design is available. Never write "
    "a project's files loosely into the workspace folder."
)


def declined(label: str) -> str:
    """What the model hears after Not now on a setup card (B161)."""
    return (f"The person chose Not now: {label} stays off. Don't ask again or turn it on another way "
            "(such as row_bot_update_setting) unless they ask.")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _conversation_id() -> str:
    from row_bot.conversation_resources import current_execution_context

    context = current_execution_context()
    if context is not None and context.conversation_id:
        return context.conversation_id
    try:
        from row_bot.agent import get_current_thread_id

        return get_current_thread_id()
    except Exception:
        return ""


def _followups_available() -> bool:
    from row_bot.runtime.executions import current_execution

    execution = current_execution()
    return bool(execution is not None and getattr(execution, "followups", False))


def _clean_name(value: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:120] or fallback


def _folder_name(name: str) -> str:
    """A portable folder name from the person's words ("Tiny date app")."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]+', " ", name)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text.split(".", 1)[0].casefold() in {
        "con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10)),
    }:
        text = "Code folder"
    return text[:60].rstrip(" .") or "Code folder"


def _free_folder(parent: Any, name: str) -> str:
    candidate, index = name, 2
    while (parent / candidate).exists():
        candidate = f"{name} {index}"
        index += 1
    return candidate


def _turn_on(group: str, label: str, why: str) -> bool:
    """Ask to turn on a tool group through the standard approval (a setup card)."""
    if registry.is_enabled(group):
        return True
    from langgraph.types import interrupt

    approved = interrupt({
        "tool": "row_bot_update_setting",
        "label": f"Turn on {label}",
        "description": why,
        "args": {"setting": "tool_toggle", "value": f"{group}:on"},
        "setup": {"kind": "tool", "label": label},
    })
    if not approved:
        return False
    registry.set_enabled(group, True)
    return registry.is_enabled(group)


def _existing(conversation_id: str, kind: str) -> Any:
    from row_bot.conversation_resources import describe, list_bindings

    for binding in list_bindings(conversation_id).bindings:
        if binding.kind == kind:
            return binding, describe(binding)
    return None


def _setup(conversation_id: str, command_id: str, payload: dict[str, Any], *,
           authorized_folder: Any = None) -> dict[str, Any]:
    from row_bot.application.client_platform import ClientPlatformError, client_platform_service as service

    last_error: Exception | None = None
    for _ in range(3):
        command = {
            "type": "resource.setup",
            "command_id": command_id,
            "expected_revision": str(service._metadata(conversation_id)["client_revision"]),
            "payload": payload,
        }
        try:
            return service.execute(owner_id=f"conversation-tool:{conversation_id}", idempotency_key=command_id,
                                   command=command, target=conversation_id,
                                   authorized_folder=authorized_folder)
        except ClientPlatformError as error:
            last_error = error
            if str(error) not in {"revision_conflict", "idempotency_mismatch"}:
                break
            # The conversation moved (a rename, a draft): retry with a fresh id.
            command_id = str(uuid.uuid4())
    raise last_error or RuntimeError("resource_setup_failed")


def _command_id(conversation_id: str, kind: str, name: str) -> str:
    from row_bot.runtime.executions import current_execution

    execution = current_execution()
    turn = execution.generation_id if execution is not None else str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"row-bot:{conversation_id}:tool:{turn}:{kind}:{name}"))


def _continue_after(conversation_id: str, prompt: str, note: str) -> bool:
    if not _followups_available():
        return False
    from row_bot.application.conversation_followups import Followup, schedule

    schedule(conversation_id, Followup(kind="resource", prompt=prompt, note=note))
    return True


def _created(kind: str, result: dict[str, Any], name: str, continues: bool) -> str:
    noun = "design" if kind == "design" else "code folder"
    return _json({
        "ok": True,
        "kind": "resource_created",
        "resource_kind": kind,
        "resource_id": str(result.get("resource_id") or ""),
        "binding_id": str(result.get("binding_id") or ""),
        "name": name,
        "display_summary": f"Created {noun} “{name}”",
        "next": (
            "Stop here and end this reply with one short sentence. The work continues by itself in "
            f"the next step, where the {noun} is available."
            if continues
            else f"The {noun} is ready. Continue in your next reply."
        ),
    })


def create_design(design_type: DesignType = "deck", name: str = "", brief: str = "") -> str:
    """Create a design for this conversation and continue in it next."""
    conversation_id = _conversation_id()
    if not conversation_id:
        return _json({"ok": False, "error": "No conversation is active."})
    mode = design_type if design_type in _DESIGN_WORDS else "deck"
    title = _clean_name(name, f"New {_DESIGN_WORDS[mode]}")
    if not _turn_on("designer", "Designer", "Row-Bot needs Designer to make this. Turning it on "
                    "lets Row-Bot create and edit designs; you can turn it off in Settings › Tools."):
        return _json({"ok": False, "kind": "setup_declined",
                      "error": declined("Designer") + " Answer in the chat without creating a design."})
    command_id = _command_id(conversation_id, "design", title)
    payload = {"kind": "artifact", "intent": "create",
               "artifact": {"mode": mode, "name": title, "brief": str(brief or "")[:20000]}}
    try:
        result = _setup(conversation_id, command_id, payload)
    except Exception as error:
        logger.warning("create_design failed for %s: %s", conversation_id, error)
        return _json({"ok": False, "error": "The design couldn't be created. Tell the person and offer "
                      "Add resource."})
    if result.get("status") != "completed":
        return _json({"ok": False, "error": "The design was only partly created. Ask the person to "
                      "check it under Context › Working on."})
    brief_text = str(brief or "").strip()
    continues = _continue_after(
        conversation_id,
        "[Continue in the new design]\n"
        f"The {_DESIGN_WORDS[mode]} “{title}” is now open for this conversation. Draft it now"
        + (f" from this brief:\n{brief_text[:4000]}" if brief_text else " from the person's request above.")
        + "\nUse the designer tools; keep the chat reply short.",
        f"Continuing in {title}",
    )
    return _created("design", result, title, continues)


def create_code_folder(name: str = "") -> str:
    """Create a code folder in Drafts for this conversation and continue in it next."""
    conversation_id = _conversation_id()
    if not conversation_id:
        return _json({"ok": False, "error": "No conversation is active."})
    existing = _existing(conversation_id, "workspace")
    if existing is not None:
        return _json({"ok": False, "kind": "existing",
                      "error": f"This conversation already has the code folder “{existing[1].title}”. "
                               "Keep working in it."})
    if not _turn_on("developer", "Developer tools", "Row-Bot needs Developer tools to build this in a "
                    "code folder. Turning them on lets Row-Bot create, edit and run code in folders "
                    "you give it; you can turn them off in Settings › Tools."):
        return _json({"ok": False, "kind": "setup_declined",
                      "error": declined("Developer tools") + " Answer in the chat (explain or show short "
                               "code) and do not write files into the workspace."})
    from row_bot.application.conversation_creation import _draft_parent

    try:
        parent = _draft_parent()
    except Exception:
        return _json({"ok": False, "error": "The workspace folder isn't available. Ask the person to "
                      "check Settings › System › Workspace folder."})
    title = _free_folder(parent.path, _folder_name(_clean_name(name, "Code folder")))
    command_id = _command_id(conversation_id, "code", title)
    payload = {"kind": "workspace", "intent": "create", "empty_workspace": {"folder_name": title}}
    try:
        result = _setup(conversation_id, command_id, payload, authorized_folder=parent)
    except Exception as error:
        logger.warning("create_code_folder failed for %s: %s", conversation_id, error)
        return _json({"ok": False, "error": "The code folder couldn't be created. Tell the person and "
                      "offer Add resource."})
    if result.get("status") != "completed":
        return _json({"ok": False, "error": "The code folder was only partly created. Ask the person to "
                      "check it under Context › Working on."})
    continues = _continue_after(
        conversation_id,
        "[Continue in the new code folder]\n"
        f"The code folder “{title}” is ready for this conversation. Continue the person's request now: "
        "create and edit its files with the Developer tools, then say briefly what you built and how "
        "to run it.",
        f"Continuing in {title}",
    )
    return _created("code", result, title, continues)


def request_connection(service: Connection, reason: str = "") -> str:
    """Show a Connect card for an account or channel the work needs."""
    label, page = _CONNECTIONS.get(str(service), ("", ""))
    if not label:
        return _json({"ok": False, "error": "Unknown connection."})
    return _json({
        "ok": True,
        "kind": "setup_needed",
        "setup_kind": "connection",
        "target": str(service),
        "label": label,
        "settings_page": page,
        "reason": str(reason or "")[:300],
        "display_summary": f"Asked to connect {label}",
        "next": f"The person sees a Connect {label} card. Tell them what you will do once it is "
                "connected, then stop.",
    })


class _DesignInput(BaseModel):
    design_type: DesignType = Field(
        default="deck",
        description="deck (slides, presentation), document (report, one-pager), landing (web page), "
                    "app_mockup (app screens, poster, social post) or storyboard.",
    )
    name: str = Field(default="", description="A short human name from the request, e.g. "
                                               "“Harbour cleanup deck”.")
    brief: str = Field(default="", description="What it should contain; drafted in the next step.")


class _CodeFolderInput(BaseModel):
    name: str = Field(default="", description="A short folder name from the request, e.g. “Tiny date app”.")


class _ConnectionInput(BaseModel):
    service: Connection = Field(description="The account or channel the work needs.")
    reason: str = Field(default="", description="One short sentence on why it is needed.")


class ConversationSetupTool(BaseTool):
    @property
    def name(self) -> str:
        return "conversation_setup"

    @property
    def display_name(self) -> str:
        return "🧩 Designs and code folders"

    @property
    def description(self) -> str:
        return ("Create a design or a code folder for this conversation when the work needs one, "
                "or ask the person to connect an account.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    def execute(self, query: str) -> str:
        return "Use create_design, create_code_folder or request_connection."

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=create_design,
                name="create_design",
                description=(
                    "Create a design (deck, document, landing page, app mockup or storyboard) for this "
                    "conversation when the person asks for one. It appears as a card with Open, Rename "
                    "and Undo; the drafting continues by itself in the next step. Not for images, "
                    "charts or answers that belong in the chat."
                ),
                args_schema=_DesignInput,
            ),
            StructuredTool.from_function(
                func=create_code_folder,
                name="create_code_folder",
                description=(
                    "Create a code folder (in Drafts) for this conversation when the person asks you to "
                    "build or make an app, site, script or other code project. It appears as a card "
                    "with Open, Rename and Undo; the building continues by itself in the next step. "
                    "Not for questions, a short snippet in the chat, workflows, or results they want "
                    "in the chat or app only."
                ),
                args_schema=_CodeFolderInput,
            ),
            StructuredTool.from_function(
                func=request_connection,
                name="request_connection",
                description=(
                    "When the work needs an account or channel that is not connected (Google for "
                    "Gmail or Calendar, GitHub, X, or a messaging channel), show the person a Connect "
                    "card instead of sending them to Settings."
                ),
                args_schema=_ConnectionInput,
            ),
        ]


registry.register(ConversationSetupTool())
