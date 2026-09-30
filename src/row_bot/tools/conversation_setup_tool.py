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

``use_code_folder`` and ``clone_repository`` bring in a folder the person
already has or a repository (B277). The model never gives a path: a folder the
person registered before is bound by its exact name through the same binding
Add resource's reuse list makes; anything else pauses the turn on a card where
the person picks the folder (or the parent to clone into) with the same picker
and reviewed ``resource.setup`` path as Add resource. Their answer resumes the
turn, which captures the new binding when it starts again, so the work goes on
in the folder at once. A tool re-runs from the top when its turn resumes, so a
folder already bound is always read first.
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
    "a project's files loosely into the workspace folder. EXISTING FOLDERS AND REPOSITORIES: when "
    "they want you to work on a folder or project they already have, call use_code_folder (with its "
    "name if they gave one); when they give a Git repository to clone or work on, call "
    "clone_repository with its URL. Never run git clone in the shell and never ask for a folder "
    "path: the person chooses the folder on a card, and you work only in the conversation's code "
    "folder."
)
# A name that reads as a path is never used: the person picks folders.
_PATH_LIKE = re.compile(r"[\\/]|^~|^[A-Za-z]:")
_MAX_FOLDER_CHOICES = 8


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
    from row_bot.application.conversation_creation import _draft_parent, code_folder_name, free_folder_name

    try:
        parent = _draft_parent()
    except Exception:
        return _json({"ok": False, "error": "The workspace folder isn't available. Ask the person to "
                      "check Settings › System › Workspace folder."})
    title = free_folder_name(parent.path, code_folder_name(name))
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


_NOTHING_CHOSEN = ("The person didn't choose a folder, so nothing was added. Don't ask again or reach a "
                   "folder another way (such as the shell) unless they ask; answer in the chat.")


def _bound_folder(conversation_id: str, asked: str = "") -> str | None:
    """The conversation's code folder as a card, and how the work goes on in it."""
    existing = _existing(conversation_id, "workspace")
    if existing is None:
        return None
    from row_bot.conversation_resources import current_execution_context

    binding, descriptor = existing
    title = descriptor.title
    context = current_execution_context()
    note = ""
    if asked and asked.casefold() != title.casefold():
        note = (f" A conversation works in one code folder: to use “{asked}” instead, the person "
                f"removes “{title}” under Context › Working on first.")
    if context is not None and binding in context.bindings:
        # Bound before this turn (or its resumed part) started: usable now.
        step = "It is ready now: continue the person's request in it with the Developer tools."
    elif _continue_after(
        conversation_id,
        "[Continue in the code folder]\n"
        f"The code folder “{title}” is now bound to this conversation. Continue the person's request "
        "now: work in it with the Developer tools, then say briefly what you did.",
        f"Continuing in {title}",
    ):
        step = ("Stop here and end this reply with one short sentence. The work continues by itself in "
                "the next step, where the code folder is available.")
    else:
        step = "It is ready. Continue in your next reply."
    return _json({
        "ok": True,
        "kind": "resource_bound",
        "resource_kind": "code",
        "resource_id": binding.resource_id,
        "binding_id": binding.binding_id,
        "name": title,
        "display_summary": f"Using code folder “{title}”",
        "next": f"This conversation works in the code folder “{title}”.{note} {step}",
    })


def _registered_folders() -> list[Any]:
    """The code folders Add resource's reuse list offers, available ones only."""
    from row_bot.developer.client_workspace import list_workspace_choices

    folders: list[Any] = []
    cursor = None
    while True:
        page = list_workspace_choices(cursor, limit=100)
        folders.extend(item for item in page.items if item.available)
        cursor = page.next_cursor
        if not cursor:
            return folders


def _named(name: str, folders: list[Any]) -> tuple[list[Any], list[Any]]:
    """Folders called exactly ``name`` (any case), else those whose name contains it."""
    wanted = name.casefold()
    names = [(" ".join(folder.name.split()).casefold(), folder) for folder in folders]
    exact = [folder for text, folder in names if text == wanted]
    return exact, ([] if exact else [folder for text, folder in names if wanted in text])


def _ask_developer_tools() -> str | None:
    if _turn_on("developer", "Developer tools", "Row-Bot needs Developer tools to work in a code "
                "folder. Turning them on lets Row-Bot create, edit and run code in folders you give "
                "it; you can turn them off in Settings › Tools."):
        return None
    return _json({"ok": False, "kind": "setup_declined",
                  "error": declined("Developer tools") + " Answer in the chat and do not reach the "
                           "folder another way (such as the shell)."})


def use_code_folder(name: str = "") -> str:
    """Work in a code folder the person already has: bound by name, else picked on a card."""
    conversation_id = _conversation_id()
    if not conversation_id:
        return _json({"ok": False, "error": "No conversation is active."})
    asked = _clean_name(name, "")
    if _PATH_LIKE.search(asked):
        asked = ""
    bound = _bound_folder(conversation_id, asked)
    if bound is not None:
        return bound
    refused = _ask_developer_tools()
    if refused is not None:
        return refused
    exact, partial = _named(asked, _registered_folders()) if asked else ([], [])
    if len(exact) == 1:
        folder = exact[0]
        payload = {"kind": "workspace", "intent": "add", "resource_id": folder.resource_id,
                   "expected_resource_revision": folder.revision}
        try:
            result = _setup(conversation_id, _command_id(conversation_id, "use", folder.resource_id), payload)
        except Exception as error:
            logger.warning("use_code_folder failed for %s: %s", conversation_id, error)
            result = {}
        bound = _bound_folder(conversation_id, asked) if result.get("status") == "completed" else None
        return bound or _json({"ok": False, "error": f"The code folder “{folder.name}” couldn't be added. "
                                                     "Tell the person and offer Add resource."})
    choices = (exact or partial)[:_MAX_FOLDER_CHOICES]
    if len(exact) > 1:
        reason = f"More than one code folder is called “{asked}”. Choose the one to work in."
    elif choices:
        reason = f"No code folder is called exactly “{asked}”. Choose one of these, or another folder."
    elif asked:
        reason = (f"No code folder is called “{asked}” yet. Choose the folder on this computer; Row-Bot "
                  "adds it and works only inside it.")
    else:
        reason = ("Choose the folder on this computer; Row-Bot adds it and works only inside it. Its "
                  "files and Git history stay as they are.")
    from langgraph.types import interrupt

    interrupt({
        "tool": "use_code_folder",
        "label": "Use an existing folder",
        "description": reason,
        "args": {"name": asked} if asked else {},
        "setup": {"kind": "folder", "label": "Use an existing folder",
                  "folders": [{"resource_id": folder.resource_id, "name": folder.name,
                               "revision": folder.revision} for folder in choices]},
    })
    # The card binds what the person picked before the turn goes on.
    return _bound_folder(conversation_id, asked) or _json({"ok": False, "kind": "setup_declined",
                                                           "error": _NOTHING_CHOSEN})


def clone_repository(repo_url: str = "") -> str:
    """Clone a repository into a folder the person chooses on a card, and work in it."""
    conversation_id = _conversation_id()
    if not conversation_id:
        return _json({"ok": False, "error": "No conversation is active."})
    from row_bot.developer.client_clone import CloneCreationError, source_name
    from row_bot.developer.storage import get_workspace

    try:
        source, folder_name = source_name(str(repo_url or "").strip())
    except CloneCreationError:
        return _json({"ok": False, "kind": "invalid_repository",
                      "error": "Row-Bot can't clone that. It needs a Git repository address starting "
                               "https://, ssh:// or git@, with no user name, password or token in it. "
                               "Ask the person for the repository's address."})

    def cloned() -> str | None:
        existing = _existing(conversation_id, "workspace")
        if existing is None:
            return None
        workspace = get_workspace(existing[0].resource_id)
        if workspace is not None and workspace.repo_url == source:
            return _bound_folder(conversation_id)
        return _json({"ok": False, "kind": "existing",
                      "error": f"This conversation already has the code folder “{existing[1].title}”. A "
                               "conversation works in one code folder: clone in a new conversation, or "
                               "keep working in this one."})

    done = cloned()
    if done is not None:
        return done
    refused = _ask_developer_tools()
    if refused is not None:
        return refused
    from langgraph.types import interrupt

    interrupt({
        "tool": "clone_repository",
        "label": f"Clone {folder_name}",
        "description": (f"Row-Bot downloads it into a new folder “{folder_name}” inside the folder you "
                        "choose, with this computer's own Git sign-in. Nothing else there changes."),
        "args": {"repo_url": source},
        "setup": {"kind": "clone", "label": folder_name, "repo_url": source},
    })
    # The card clones through Add resource's reviewed path and binds the result.
    return cloned() or _json({"ok": False, "kind": "setup_declined", "error": _NOTHING_CHOSEN})


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


class _UseFolderInput(BaseModel):
    name: str = Field(default="", description="The folder's name as the person calls it, e.g. “tide-app”; "
                                               "empty to let them choose. Never a path.")


class _CloneInput(BaseModel):
    repo_url: str = Field(description="The repository's address, e.g. "
                                      "“https://github.com/owner/project.git” or “git@github.com:owner/project.git”.")


class ConversationSetupTool(BaseTool):
    @property
    def name(self) -> str:
        return "conversation_setup"

    @property
    def display_name(self) -> str:
        return "🧩 Designs and code folders"

    @property
    def description(self) -> str:
        return ("Create a design or a code folder for this conversation when the work needs one, use a "
                "folder the person already has, clone a repository, or ask the person to connect an "
                "account.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    def execute(self, query: str) -> str:
        return ("Use create_design, create_code_folder, use_code_folder, clone_repository or "
                "request_connection.")

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
                    "in the chat or app only, and not for a folder they already have."
                ),
                args_schema=_CodeFolderInput,
            ),
            StructuredTool.from_function(
                func=use_code_folder,
                name="use_code_folder",
                description=(
                    "Work on a folder or project the person already has, in this conversation. With "
                    "its name, a code folder they added before with exactly that name is used at once; "
                    "otherwise they get a card to pick it (or choose the folder on this computer) and "
                    "the work goes on in it. Use this instead of asking for a path or reading folders "
                    "with the shell."
                ),
                args_schema=_UseFolderInput,
            ),
            StructuredTool.from_function(
                func=clone_repository,
                name="clone_repository",
                description=(
                    "Clone a Git repository the person names (an https://, ssh:// or git@ address) and "
                    "work in it in this conversation. They get a card with the address and choose where "
                    "it goes; the clone uses this computer's own Git sign-in and the work goes on in it. "
                    "Use this instead of git clone in the shell."
                ),
                args_schema=_CloneInput,
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
