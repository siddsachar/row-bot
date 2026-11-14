"""Delivery: each moment fires on exactly one surface, picked from where the person is.

At the computer:

- **Buddy** (``proactive.moment`` on Buddy's event bus): its "something for you" glance and bubble, when
  Buddy is on;
- otherwise a **desktop notification** for something urgent, and an **in-app notice** for the rest.

Away (idle for a while or locked; known only with desktop context on):

- a **chat app** (the channel chosen in Settings › Proactive), only for kinds the person marked urgent;
- else **phone push**: Row-Bot's content-free wake-up to the person's subscribed phones (the phone shows
  Row-Bot's own fixed words; no moment text leaves the computer that way);
- else it waits until they're back.

Every moment is also listed in For you; that isn't a surface. The Buddy payload (``buddy_payload``) is what
the client draws: ``moment_id``, ``kind``, ``title``, ``urgency`` and up to two ``actions``.
"""
from __future__ import annotations

import logging
from typing import Any

from row_bot.proactive.model import Desktop

logger = logging.getLogger(__name__)

TITLE = "Row-Bot"


def buddy_on() -> bool:
    from row_bot.buddy.config import get_buddy_config

    return bool(get_buddy_config().get("enabled"))


def push_ready() -> bool:
    from row_bot.access.push import PushStore

    store = PushStore()
    return store.subscriptions_path.exists() and bool(store.subscriptions())


def channel_ready(settings: dict[str, Any]) -> bool:
    return _channel(settings) is not None


def _channel(settings: dict[str, Any]) -> Any | None:
    if not settings["channel"]:
        return None
    from row_bot.channels import registry

    channel = registry.get(settings["channel"])
    return channel if channel is not None and channel.is_running() else None


def pick(moment: dict[str, Any], desktop: Desktop, settings: dict[str, Any], *, buddy: bool, push: bool,
         channel: bool) -> str:
    """The one surface for a moment now, or "" when it should wait (away, and nothing reaches them)."""
    if desktop.away:
        if moment["kind"] in settings["urgent_kinds"] and channel:
            return "channel"
        return "push" if push else ""
    if buddy:
        return "buddy"
    return "desktop" if moment["urgency"] == "urgent" else "notice"


def buddy_payload(moment: dict[str, Any]) -> dict[str, Any]:
    return {"moment_id": moment["id"], "kind": moment["kind"], "title": moment["title"],
            "urgency": moment["urgency"], "actions": list(moment["actions"][:2]), "label": moment["title"]}


def send(surface: str, moment: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Put a moment on its surface; False when that surface couldn't take it (it waits for the next tick)."""
    try:
        if surface == "buddy":
            from row_bot.buddy.events import BuddyEventType, emit_buddy_event

            emit_buddy_event(BuddyEventType.PROACTIVE_MOMENT, source="proactive", payload=buddy_payload(moment))
        elif surface == "notice":
            from row_bot.application.app_notices import app_notices

            app_notices.post(title=moment["title"], message=moment["body"], level="info", source="proactive",
                             requested=True)
        elif surface == "desktop":
            from row_bot.notifications import desktop_alert

            desktop_alert(moment["title"], moment["body"])
        elif surface == "push":
            from row_bot.access.push import wake_soon

            wake_soon()
        elif surface == "channel":
            channel = _channel(settings)
            if channel is None:
                return False
            channel.send_message(channel.get_default_target(), f"{TITLE}: {moment['title']}\n{moment['body']}".strip())
        else:
            return False
    except Exception:
        logger.warning("A moment couldn't go to %s", surface, exc_info=True)
        return False
    return True

