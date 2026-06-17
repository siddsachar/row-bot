"""Compact channel status monitor used by the right activity drawer."""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

from row_bot.ui.timer_utils import safe_timer

logger = logging.getLogger(__name__)


_CHANNEL_ICON_MAP = {
    "telegram": "send",
    "discord": "sports_esports",
    "slack": "tag",
    "sms": "textsms",
    "whatsapp": "forum",
}


def _fmt_ago(epoch: float | None) -> str:
    """Format seconds-since-epoch as a relative string."""
    if epoch is None:
        return ""
    import time as _time

    delta = int(_time.time() - epoch)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def build_channel_monitor(open_settings: Callable[..., None]) -> None:
    """Render the existing Channels monitor behavior in the current slot."""

    container = ui.column().classes("w-full gap-0")

    def _build_channel_monitor() -> None:
        try:
            from row_bot.channels.base import get_last_activity
            from row_bot.channels.registry import all_channels

            channels = all_channels()
        except Exception:
            logger.debug("Could not load channel monitor state", exc_info=True)
            channels = []

        container.clear()
        if not channels:
            return

        with container:
            ui.label("Channels").classes("text-xs font-bold text-grey-5").style(
                "letter-spacing: 0.8px; text-transform: uppercase;"
            )

            for channel in channels:
                is_on = channel.is_running()
                is_configured = channel.is_configured()
                if channel.name == "whatsapp" and is_configured and not is_on:
                    try:
                        from row_bot.channels.auth import get_approved_users
                        from row_bot.channels.whatsapp import SESSION_DIR, _get_user_phone

                        has_session = SESSION_DIR.exists() and any(SESSION_DIR.iterdir())
                        is_configured = bool(
                            has_session
                            or _get_user_phone()
                            or get_approved_users("whatsapp")
                        )
                    except Exception:
                        is_configured = False

                if is_on:
                    dot_color = "#4caf50"
                    status_text = _fmt_ago(get_last_activity(channel.name)) or "Running"
                elif is_configured:
                    dot_color = "#ff9800"
                    status_text = "Stopped"
                else:
                    dot_color = "#666"
                    status_text = "Off"

                icon_name = _CHANNEL_ICON_MAP.get(channel.name, "chat")

                def _channel_click(_event, _channel=channel) -> None:
                    open_settings("Channels")

                with ui.row().classes(
                    "w-full items-center no-wrap cursor-pointer q-py-xs q-px-sm rounded"
                ).style(
                    "min-height: 28px; gap: 6px; transition: background 0.15s;"
                ).on("click", _channel_click).on(
                    "mouseenter",
                    js_handler="(e) => e.currentTarget.style.background='rgba(255,255,255,0.06)'",
                ).on(
                    "mouseleave",
                    js_handler="(e) => e.currentTarget.style.background='transparent'",
                ):
                    ui.html(
                        f'<span style="display:inline-block;width:8px;height:8px;'
                        f'border-radius:50%;background:{dot_color};flex-shrink:0;"></span>',
                        sanitize=False,
                    )
                    ui.icon(icon_name, size="xs").classes(
                        "text-grey-5" if not is_on else "text-primary"
                    ).style("font-size: 0.85rem;")
                    ui.label(channel.display_name).classes("ellipsis").style(
                        "font-size: 0.8rem; flex-grow: 1;"
                        + ("opacity: 0.45;" if not is_configured else "")
                    )
                    ui.label(status_text).classes("text-grey-6").style(
                        "font-size: 0.7rem; flex-shrink: 0;"
                    )

    _build_channel_monitor()
    safe_timer(5.0, _build_channel_monitor)
