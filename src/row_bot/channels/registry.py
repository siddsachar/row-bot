"""
Thoth – Channel Registry
==========================
Central registry for all channel adapters.

Channels register themselves at import time.  The registry provides
lookup, auto-start, and task-delivery routing so callers don't need
to know which channels exist.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.channels.base import Channel

log = logging.getLogger("thoth.channels.registry")

# ── Internal store ───────────────────────────────────────────────────
_channels: dict[str, "Channel"] = {}


# ── Public API ───────────────────────────────────────────────────────

def register(channel: "Channel") -> None:
    """Register a channel adapter instance."""
    _channels[channel.name] = channel
    log.debug("Registered channel: %s", channel.name)


def get(name: str) -> "Channel | None":
    """Return the channel with *name*, or ``None``."""
    return _channels.get(name)


def all_channels() -> list["Channel"]:
    """Return all registered channels (insertion order)."""
    return list(_channels.values())


def running_channels() -> list["Channel"]:
    """Return only channels that are currently active."""
    return [ch for ch in _channels.values() if ch.is_running()]


def configured_channels() -> list["Channel"]:
    """Return only channels that have valid configuration."""
    return [ch for ch in _channels.values() if ch.is_configured()]


def deliver(channel_name: str, target: str | int, text: str) -> tuple[str, str]:
    """Deliver a message via *channel_name*.

    Returns
    -------
    tuple[str, str]
        ``("delivered", detail)`` on success,
        ``("delivery_failed", reason)`` on error.
    """
    ch = _channels.get(channel_name)
    if ch is None:
        return "delivery_failed", f"Unknown channel: {channel_name}"
    if not ch.is_running():
        return "delivery_failed", f"{ch.display_name} is not running"
    try:
        ch.send_message(target, text)
        return "delivered", f"Delivered via {ch.display_name}"
    except Exception as exc:
        log.warning("Delivery via %s failed: %s", channel_name, exc)
        return "delivery_failed", f"{ch.display_name} delivery failed: {exc}"


def validate_delivery(channel_name: str | None,
                      target: str | None) -> None:
    """Raise ``ValueError`` if delivery settings are invalid.

    Called by ``tasks.py`` when creating/updating a task.
    """
    if not channel_name and not target:
        return  # no delivery — valid

    if channel_name and not target and channel_name != "telegram":
        raise ValueError(
            f"delivery_channel is '{channel_name}' but delivery_target is empty."
        )
    if target and not channel_name:
        raise ValueError(
            "delivery_target is set but delivery_channel is empty."
        )

    ch = _channels.get(channel_name)
    if ch is None:
        # Accept channel names we haven't loaded yet (e.g. tests)
        known = list(_channels.keys())
        raise ValueError(
            f"Unknown delivery channel '{channel_name}'. "
            f"Registered: {known}"
        )

    # Channel-specific validation (Telegram doesn't need a target)
    if channel_name == "telegram":
        pass  # uses TELEGRAM_USER_ID at delivery time
    elif "@" in str(target or ""):
        # Looks like an email — accept on channels that can handle it
        pass
    elif target:
        pass  # trust the target for other channels


def _reset() -> None:
    """Clear all channels (testing only)."""
    _channels.clear()
