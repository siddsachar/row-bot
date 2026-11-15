"""The editors Row-Bot connects to, the tools they see and the words they get back.

Both sides read this: the bridge lists the tools without asking the app (an editor sees them
while Row-Bot is closed), and the app runs them. Every text here is read by the editor's model.
"""

from __future__ import annotations

from dataclasses import dataclass

SERVER_NAME = "row-bot"
# The MCP versions the bridge speaks, newest first (it answers in the editor's when it knows it).
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

MAX_MESSAGE_CHARS = 20_000
MAX_FOLDER_CHARS = 1_024
MAX_QUERY_CHARS = 500
# A turn waits this long for its answer before the editor hears it is still working: under the 10
# minutes Codex is given in its snippet. Zed stops a call after 60 seconds unless told otherwise.
ANSWER_WAIT_SECONDS = 540
ZED_ANSWER_WAIT_SECONDS = 50


@dataclass(frozen=True)
class Editor:
    id: str
    name: str
    # Row-Bot may write its entry into this editor's own config file (Zed's allows comments: shown only).
    writes_config: bool
    answer_wait_seconds: float = ANSWER_WAIT_SECONDS


EDITORS: dict[str, Editor] = {
    editor.id: editor
    for editor in (
        Editor("claude-code", "Claude Code", True),
        Editor("cursor", "Cursor", True),
        Editor("codex", "Codex", True),
        Editor("zed", "Zed", False, ZED_ANSWER_WAIT_SECONDS),
    )
}


def editor(editor_id: object) -> Editor | None:
    return EDITORS.get(str(editor_id or ""))


# --- What the editor gets back ------------------------------------------------------------------------

NOT_RUNNING = "Row-Bot isn't running. Open Row-Bot on this computer, then try again."
NOT_CONNECTED = ("This editor isn't connected to Row-Bot. Connect it in Row-Bot: Settings › Devices & remote "
                 "access › Connect an editor.")
WAITING = "Waiting for your approval in Row-Bot"
BUSY = "Row-Bot is still answering in this chat. Try again when it has finished."
NO_MODEL = "Row-Bot has no model to answer with yet. Choose one in Row-Bot, then try again."
STOPPED = "Stopped in Row-Bot before it finished."
NOT_CONTINUABLE = "That conversation can't be continued from an editor. Use one from list_conversations."
NO_ANSWER = "Row-Bot finished without an answer."


def still_working(title: str, conversation_id: str) -> str:
    return (f"Row-Bot is still working on this. Its answer will be in Row-Bot, in the chat \"{title}\" "
            f"(conversation_id: {conversation_id}).")


def waiting(summary: str) -> str:
    summary = " ".join(str(summary or "").split())[:200]
    return f"{WAITING}: {summary}" if summary else WAITING + "."


def folder_not_used(folder: str) -> str:
    return (f"Row-Bot didn't work in {folder}: it isn't one of Row-Bot's code folders. To let Row-Bot work "
            "there, add the folder to this chat in Row-Bot.")


def failed(reason: str) -> str:
    reason = " ".join(str(reason or "").split())[:300]
    return f"Row-Bot couldn't answer: {reason}" if reason else "Row-Bot couldn't answer."


# --- The tools ------------------------------------------------------------------------------------------

TOOLS: tuple[dict, ...] = (
    {
        "name": "ask_row_bot",
        "title": "Ask Row-Bot",
        "description": (
            "Ask Row-Bot, the person's own assistant on this computer, to answer a question or do a task. "
            "Row-Bot has the person's memory, apps and tools, and can work in their code folders. Anything "
            "that needs the person's approval waits for them in Row-Bot. Each editor and project folder has "
            "its own Row-Bot chat, which keeps the conversation going between calls; pass conversation_id "
            "(from list_conversations) to continue another chat instead. Returns Row-Bot's answer, or "
            "\"" + WAITING + "\" when the person needs to approve something there first; ask again later "
            "for the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "maxLength": MAX_MESSAGE_CHARS,
                            "description": "What to ask Row-Bot, in plain words."},
                "conversation_id": {"type": "string", "maxLength": 128,
                                    "description": "A chat from list_conversations to continue, instead of "
                                                   "the chat for this editor and folder."},
                "folder": {"type": "string", "maxLength": MAX_FOLDER_CHARS,
                           "description": "The absolute path of the project folder you are working in. "
                                          "Row-Bot works in it when it is one of Row-Bot's code folders."},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Ask Row-Bot", "readOnlyHint": False, "destructiveHint": False,
                        "openWorldHint": True},
    },
    {
        "name": "search_memory",
        "title": "Search Row-Bot's memory",
        "description": (
            "Search what Row-Bot remembers about the person: the memories and knowledge it has saved. "
            "Read-only: nothing is saved or changed. Returns up to 10 matches, best first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": MAX_QUERY_CHARS,
                          "description": "What to look for, in a few words."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Search Row-Bot's memory", "readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_conversations",
        "title": "List Row-Bot chats",
        "description": (
            "List the person's recent Row-Bot chats that ask_row_bot may continue, newest first: each one's "
            "conversation_id, title and when it was last used. Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "List Row-Bot chats", "readOnlyHint": True, "openWorldHint": False},
    },
)

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
