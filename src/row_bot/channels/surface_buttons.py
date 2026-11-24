"""An interface's buttons in chat apps (generative UI, G7).

Telegram, Slack and Discord show an interface Row-Bot drew as its words (``components_as_text``) and, after
the reply, its buttons. A tap is the person's press on that interface: the same A2UI action message the app
sends, checked against the drawing kept in the conversation, with the chat showing the button's words. Like
a press in the app it is the person's answer there, never approval of a tool call.

Tokens live in memory for a day: after a restart a tap says so, and the person asks again.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

log = logging.getLogger(__name__)

MAX_BUTTONS = 8
TOKEN_SECONDS = 24 * 60 * 60
MAX_TOKENS = 512

_lock = threading.Lock()
_tokens: dict[str, dict[str, Any]] = {}
_answering: set[str] = set()


@dataclass(frozen=True)
class Offer:
    """One interface's buttons, sent after the reply that drew it: its title and (label, token) pairs."""

    title: str
    buttons: list[tuple[str, str]]


@dataclass(frozen=True)
class Press:
    """A tap, checked: what the agent reads, what the chat shows, and the button's words."""

    model_text: str
    shown: str
    label: str


class Refused(Exception):
    """A tap that can't be a press; its message says why, in words for the chat."""


def _forget_old(now: float) -> None:
    for token in [token for token, entry in _tokens.items() if now - entry["at"] > TOKEN_SECONDS]:
        _tokens.pop(token, None)
    while len(_tokens) >= MAX_TOKENS:
        _tokens.pop(next(iter(_tokens)))


def offers(channel: str, thread_id: str) -> list[Offer]:
    """The buttons of each interface the latest reply drew in this conversation, ready to send.

    The reply is everything after the person's latest message; a redrawn surface offers its latest drawing.
    """

    from langchain_core.messages import AIMessage, HumanMessage

    from row_bot import threads
    from row_bot.application import surfaces

    if not thread_id:
        return []
    try:
        messages = threads.get_latest_checkpoint_messages(thread_id)
    except Exception:
        log.debug("A chat app's interface buttons could not read the conversation", exc_info=True)
        return []
    reply: list[Any] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        reply.append(message)
    drawn: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in reversed(reply):
        if isinstance(message, AIMessage) and message.id:
            for surface in surfaces.surfaces_in(message):
                drawn[surface["surfaceId"]] = (str(message.id), surface)
    found: list[Offer] = []
    now = time.time()
    with _lock:
        _forget_old(now)
        for surface_id, (message_id, surface) in drawn.items():
            buttons: list[tuple[str, str]] = []
            for button in surfaces.chat_app_buttons(surface, MAX_BUTTONS):
                token = secrets.token_urlsafe(9)
                _tokens[token] = {"channel": channel, "thread_id": thread_id, "message_id": message_id,
                                  "surface_id": surface_id, "at": now, **button}
                buttons.append((button["label"], token))
            if buttons:
                found.append(Offer(title=surface["title"], buttons=buttons))
    return found


def take(token: str, *, channel: str, thread_id: str) -> Press:
    """The press a tap makes in this chat's conversation; raises :class:`Refused` with why it can't."""

    from row_bot.application import surfaces

    with _lock:
        entry = _tokens.get(str(token or ""))
    if entry is None or time.time() - entry["at"] > TOKEN_SECONDS or entry["channel"] != channel:
        raise Refused("That button has expired. Ask Row-Bot to show the interface again.")
    if entry["thread_id"] != thread_id:
        raise Refused("That button belongs to an earlier conversation in this chat.")
    surface = surfaces.reply_surface(thread_id, entry["message_id"], entry["surface_id"])
    if surface is None:
        raise Refused("The reply that drew that interface is no longer in this conversation.")
    try:
        model_text, shown = surfaces.action_message(
            surface, entry["name"], entry["context"], entry["component_id"],
            datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except surfaces.SurfaceError:
        raise Refused("That button no longer matches its interface.") from None
    return Press(model_text=model_text, shown=shown, label=entry["label"])


@contextmanager
def answering(thread_id: str) -> Iterator[bool]:
    """Whether this press may run now: False while Row-Bot still answers an earlier press there."""

    with _lock:
        free = thread_id not in _answering
        if free:
            _answering.add(thread_id)
    try:
        yield free
    finally:
        if free:
            with _lock:
                _answering.discard(thread_id)


def press_config(config: dict, press: Press) -> dict:
    """The turn's config for a press: the chat shows the button's words; the note keeps it from ever
    counting as the person approving a tool call (as for a press in the app)."""

    configurable = dict(config.get("configurable") or {})
    configurable["platform_task_note"] = {"kind": "surface", "text": press.shown}
    return {**config, "configurable": configurable}
