"""Linked conversations (5.3.1 C): start or propose a conversation, message another, list them."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _refused_here() -> str:
    """Helpers do quick sub-tasks in their parent's turn; a conversation of its own starts from a chat."""
    from row_bot.agent import get_active_runtime_context

    surface = str((get_active_runtime_context() or {}).get("runtime_surface") or "")
    if surface in {"agent", "agent_child"}:
        return "A helper can't start or message conversations: say what's needed in your result instead."
    return ""


class _StartInput(BaseModel):
    title: str = Field(description="A few words.")
    message: str = Field(description="Its first message, with everything the work needs.")
    mode: Literal["fresh", "fork"] = Field(default="fresh", description="fork adds this chat's recent messages.")
    propose: bool = Field(default=False, description="Only offer it: the person starts it with one tap.")
    approval_mode: Literal["", "block", "approve", "auto"] = Field(
        default="", description="A stricter approval level; empty keeps this chat's.")


class _SendInput(BaseModel):
    conversation: str = Field(description="The conversation to message: its name or id (a workflow's too).")
    message: str = Field(description="The message. It arrives there as a message from this conversation.")
    reply: bool = Field(default=True, description="false: an update that needs no answer.")
    when: Literal["now", "next_run"] = Field(default="now", description="next_run: a workflow reads it on its next run.")


def _start(title: str, message: str, mode: str = "fresh", propose: bool = False, approval_mode: str = "") -> str:
    from row_bot import linked_conversations as linked

    if refused := _refused_here():
        return refused
    try:
        card = linked.start(title, message, mode=mode, propose=propose, approval_mode=approval_mode)
    except linked.LinkError as error:
        return f"Not started: {error}"
    if card["proposed"]:
        next_step = ("The person sees a card to start it or say not now. Mention it in one sentence; don't "
                     "start it yourself.")
    else:
        next_step = ("It's working now and shows in the sidebar. Carry on here; use conversation_links to see "
                     "its state, or conversation_send to message it.")
    return _json({"ok": True, "kind": "linked_conversation", "link_id": card["link_id"], "title": card["title"],
                  "conversation_id": card["conversation_id"], "state": card["state"], "proposed": card["proposed"],
                  "display_summary": ("Proposed: " if card["proposed"] else "Started: ") + card["title"],
                  "next": next_step})


def _send(conversation: str, message: str, reply: bool = True, when: str = "now") -> str:
    from row_bot import linked_conversations as linked

    if refused := _refused_here():
        return refused
    try:
        return linked.send(conversation, message, reply=reply, when=when)  # type: ignore[arg-type]
    except linked.LinkError as error:
        return f"Not sent: {error}"


def _links() -> str:
    from row_bot import linked_conversations as linked

    try:
        source = linked.origin()
    except linked.LinkError as error:
        return str(error)
    found = linked.links(source.conversation_id)
    if not found:
        return "This conversation hasn't started or proposed any conversations."
    words = {"proposed": "proposed, waiting for the person", "working": "working", "waiting": "waiting for the person",
             "done": "done", "dismissed": "the person said not now", "expired": "proposal expired"}
    return "\n".join(f"- {card['title']}: {words.get(card['state'], card['state'])}"
                     + (f" (id {card['conversation_id']})" if card["conversation_id"] else "") for card in found)


class LinkedConversationsTool(BaseTool):

    @property
    def name(self) -> str:
        return "linked_conversations"

    @property
    def display_name(self) -> str:
        return "🔗 Linked conversations"

    @property
    def description(self) -> str:
        return ("Start or propose a conversation of its own for longer work, message another conversation and "
                "hear back, and list them.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_start, name="conversation_start", args_schema=_StartInput,
                description=("Only when the person asks for a separate conversation: start one that runs now, "
                             "which they can open and stop. Otherwise do the work here (a helper for a sub-task).")),
            StructuredTool.from_function(
                func=_send, name="conversation_send", args_schema=_SendInput,
                description=("Message another conversation (a workflow's too) by name or id. It starts a turn "
                             "there, and its reply comes back here; reply=false for updates that need no answer. "
                             "Don't answer a reply just to acknowledge it.")),
            StructuredTool.from_function(
                func=_links, name="conversation_links",
                description="List the conversations this one started or proposed, with their state."),
        ]

    def execute(self, query: str) -> str:
        return _links()


registry.register(LinkedConversationsTool())
