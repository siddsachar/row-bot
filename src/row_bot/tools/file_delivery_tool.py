"""Send files from the chat (F7): one ``send_file`` tool, to the person's devices or to a chat app.

- ``to=devices``: the files show in this conversation as downloads on every device signed in to
  Row-Bot, with "Row-Bot sent you …" in every open Row-Bot window (``file_deliveries``). This never
  asks, at any level (Block too): the files go only to the person's own sessions, like writing a new
  file to the chat's files (video editing's rule).
- ``to=telegram|discord|slack|whatsapp``: through the app's own ``send_document``, to this
  conversation's own chat there when it came from there, else the person's own chat there. That can
  reach other people, so it asks like sending a message: Ask asks, Auto's reviewer approves only what
  the person clearly asked for, Hands-off allows (never in a conversation other people share: those
  run on Auto at most), Block refuses.

Files are named, never given as arbitrary paths: this chat's files, recordings and video edits, and
workspace files through ``media_sources``' guards. At most ``MAX_FILES`` files per call and per reply;
each target has its own size limit, checked before anything is asked or sent.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool

APPROVAL_NOT_NEEDED_OWN_DEVICES = "Approval: not needed (your own devices)"
Destination = Literal["devices", "telegram", "discord", "slack", "whatsapp"]
_COUNT_LOCK = threading.Lock()
_REPLY_COUNTS: OrderedDict[tuple[str, str], int] = OrderedDict()


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _reply_key(conversation: str) -> tuple[str, str]:
    """This reply: the running turn's id, else a ten-minute window (a tool called outside a turn)."""

    try:
        from row_bot.agent import get_active_runtime_context

        turn = str(get_active_runtime_context().get("generation_id") or "")
    except Exception:
        turn = ""
    return conversation, turn or f"window:{int(time.time() // 600)}"


def sent_this_reply(conversation: str) -> int:
    with _COUNT_LOCK:
        return _REPLY_COUNTS.get(_reply_key(conversation), 0)


def _count(conversation: str, files: int) -> None:
    key = _reply_key(conversation)
    with _COUNT_LOCK:
        _REPLY_COUNTS[key] = _REPLY_COUNTS.pop(key, 0) + files
        while len(_REPLY_COUNTS) > 256:
            _REPLY_COUNTS.popitem(last=False)


def _what(sources) -> str:
    if len(sources) == 1:
        return sources[0].name
    return f"{len(sources)} files"


def _listing(sources) -> str:
    from row_bot.file_deliveries import size_words

    return ", ".join(f"{source.name} ({size_words(source.size_bytes)})" for source in sources)


def channel_approval(plan, sources, caption: str = "") -> dict:
    """The approval for sending these files to a chat app: what goes where, and who will see it."""

    origins = {"workspace" if source.relative else "chat" for source in sources}
    whence = ("from the workspace" if origins == {"workspace"} else "from this chat's files" if origins == {"chat"}
              else "from this chat's files and the workspace")
    one = len(sources) == 1
    who = (f"Everyone in {plan.place} will be able to open {'it' if one else 'them'}." if plan.shared
           else "It leaves Row-Bot and stays in that chat." if one
           else "They leave Row-Bot and stay in that chat.")
    words = f" with the message “{caption.strip()[:200]}”" if caption.strip() else ""
    return {
        "tool": "send_file",
        "label": f"Send files to {plan.label}",
        "action": "send_file",
        "question": f"Send {_what(sources)} to {plan.place}?",
        "description": f"Row-Bot will send {_listing(sources)}, {whence}, to {plan.place}{words}. {who}",
        "risk_class": "high" if plan.shared else "medium",
        "args": {"files": _listing(sources)},
    }


def channel_risks(plan, sources) -> tuple[str, ...]:
    risks = [f"sends files out of Row-Bot to {plan.label}"]
    if plan.shared:
        risks.append(f"other people in {plan.place} will see them")
    if any(source.relative for source in sources):
        risks.append("includes files from the workspace")
    return tuple(risks)


def _approved_by(line: str) -> str:
    from row_bot.tools.approval_gate import APPROVAL_FULL_AUTONOMY

    if line == APPROVAL_FULL_AUTONOMY:
        return "hands_off"
    if line.startswith("Approval: approved by the reviewer"):
        return "reviewer"
    return "you"


def _send_file(files: list[str] | None = None, to: str = "devices", caption: str = "") -> str:
    from row_bot import file_deliveries as deliveries
    from row_bot.full_autonomy import cap_for_conversation
    from row_bot.media_sources import SourceError
    from row_bot.tools.approval_gate import current_approval_mode, resolve_approval_line, with_approval

    conversation = _conversation()
    if not conversation:
        return "Sending files needs a conversation."
    names = [str(value).strip() for value in files or [] if str(value or "").strip()]
    if not names:
        return ("Name the files to send in files: a file in this chat by name, a workspace path, a recording "
                "(rec-…) or a video edit (edit-…).")
    if len(names) > deliveries.MAX_FILES:
        return f"At most {deliveries.MAX_FILES} files can be sent at once; nothing was sent."
    already = sent_this_reply(conversation)
    if already + len(names) > deliveries.MAX_FILES:
        return (f"At most {deliveries.MAX_FILES} files can be sent in one reply, and {already} already were; "
                "nothing more was sent. Send the rest in your next reply.")
    try:
        sources = deliveries.resolve(names, conversation)
    except SourceError as exc:
        return str(exc)

    destination = str(to or "devices").strip().casefold()
    if destination in {"devices", "device", "my devices", "your devices"}:
        delivery = deliveries.send_to_devices(conversation, sources)
        if delivery.status != "failed":
            _count(conversation, sum(item.sent for item in delivery.files))
        return with_approval(APPROVAL_NOT_NEEDED_OWN_DEVICES, deliveries.summary(delivery))

    try:
        plan = deliveries.plan_channel(conversation, destination)
    except KeyError:
        known = deliveries.available_channels()
        return (f"There's no chat app called {to} set up in Row-Bot. "
                + (f"Set up: {', '.join(known)}." if known else "None is set up yet.")
                + " Nothing was sent; to=devices sends to the person's own devices.")
    except deliveries.TargetProblem as problem:
        return deliveries.summary(deliveries.not_sent(conversation, sources, problem, channel=problem.channel,
                                                      label=problem.label))
    problem = deliveries.channel_problem(plan, sources)
    if problem is not None:
        return deliveries.summary(deliveries.not_sent(conversation, sources, problem, channel=plan.name,
                                                      label=plan.label, place=plan.place))

    # A conversation other people share never sends on Hands-off: Auto at most.
    level = cap_for_conversation(conversation, current_approval_mode())
    outcome, line = resolve_approval_line(channel_approval(plan, sources, caption), approval_mode=level,
                                          risks=channel_risks(plan, sources))
    if outcome == "block":
        return (f"BLOCKED: Sending files to {plan.label} needs approval, and this chat is on Block. Nothing was "
                "sent. Send them to the person's devices instead (to=devices), or they can change the approval "
                "level.")
    if outcome != "allow":
        return with_approval(line, f"The person didn't allow sending {_what(sources)} to {plan.place}; nothing "
                                   "was sent.")
    delivery = deliveries.send_to_channel(conversation, plan, sources, approval=_approved_by(line),
                                          caption=caption)
    _count(conversation, sum(item.sent for item in delivery.files))
    return with_approval(line, deliveries.summary(delivery))


class _SendInput(BaseModel):
    files: list[str] = Field(description=(
        "In order: a file in this chat by name, a workspace path, a recording (rec-…) or a video edit (edit-…)."))
    to: Destination = Field(default="devices", description=(
        "devices: the person's own signed-in devices. A chat app: their own chat there, or this chat if it came "
        "from there."))
    caption: str = Field(default="", description="A short message sent with the files in a chat app.")


class SendFileTool(BaseTool):

    @property
    def name(self) -> str:
        return "file_delivery"

    @property
    def display_name(self) -> str:
        return "📤 Send files"

    @property
    def description(self) -> str:
        return ("Send files from a chat to your own devices (as a download, with a notice) or to a chat app "
                "(Telegram, Discord, Slack, WhatsApp). Sending to a chat app asks first.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_send_file,
                name="send_file",
                description=(
                    "Send files to the person: to=devices puts them in this chat as downloads on every device "
                    "they're signed in on, with a notice (never asks); a chat app sends them there (may ask "
                    "first). Up to 10 files."
                ),
                args_schema=_SendInput,
            )
        ]

    def execute(self, query: str) -> str:
        return "Use send_file with its fields."


registry.register(SendFileTool())
