"""Unified notification system — desktop alerts, sounds, and in-app notices.

All background subsystems (workflows, timers) call ``notify()`` to fire
an immediate desktop notification + sound, and post an in-app notice that
every open client shows (``application.app_notices``; the React client reads
it over the event stream).
"""

from __future__ import annotations

import logging
import pathlib
import subprocess
import sys

from row_bot.runtime_paths import sounds_dir

logger = logging.getLogger(__name__)

_LEVELS = {"negative": "error", "warning": "warning"}

# ── Sound files ──────────────────────────────────────────────────────────────
_SOUNDS_DIR = sounds_dir()
_SOUND_MAP: dict[str, pathlib.Path] = {
    "workflow": _SOUNDS_DIR / "workflow.wav",
    "timer": _SOUNDS_DIR / "timer.wav",
}


def notify(
    title: str,
    message: str,
    sound: str = "default",
    toast_type: str = "positive",
    *,
    source: str = "app",
    requested: bool = False,
    in_app: bool = True,
) -> None:
    """Fire a notification through all channels.

    Parameters
    ----------
    title : str
        Notification title (shown in desktop toast and plyer).
    message : str
        Notification body text.
    sound : str
        Sound key: ``"workflow"``, ``"timer"``, or ``"default"``
        (falls back to Windows system beep).
    source : str
        Where the notice comes from (``workflow``, ``documents``, ``buddy`` …).
    requested : bool
        The person started the job this reports on. Clients always show
        warnings and errors, and information only when it was requested.
    in_app : bool
        False when the open conversation already shows the same problem.
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%I:%M %p")
    try:
        from row_bot.buddy.events import BuddyEventType, emit_buddy_event
        emit_buddy_event(
            BuddyEventType.NOTIFICATION,
            source="notifications",
            payload={"title": title, "message": message, "label": title},
        )
    except Exception:
        logger.debug("Buddy notification event failed", exc_info=True)

    # 1. Desktop notification (plyer) — immediate
    _desktop_notify(title, f"{message} ({timestamp})")

    # 2. Sound — immediate, non-blocking
    _play_sound(sound)

    # 3. In-app notice for every open client
    try:
        from row_bot.application.app_notices import app_notices

        if in_app:
                app_notices.post(title=title, message=message, level=_LEVELS.get(toast_type, "info"),
                             source=source, requested=requested)
    except Exception:
        logger.debug("In-app notice failed (non-fatal)", exc_info=True)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _desktop_notify(title: str, message: str) -> None:
    """Show a desktop notification via plyer (Windows) or osascript (macOS)."""
    if sys.platform == "darwin":
        # macOS: use native osascript — avoids pyobjus dependency
        try:
            import subprocess
            safe_title = title.replace('"', '\\"')
            safe_msg   = message.replace('"', '\\"')
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{safe_msg}" with title "{safe_title}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass
    try:
        from plyer import notification
        # Windows balloon tips limit message to 256 chars
        if len(message) > 253:
            message = message[:253] + "…"
        notification.notify(
            title=title,
            message=message,
            app_name="Row-Bot",
            timeout=15,
        )
    except Exception:
        logger.debug("Desktop notification failed (non-fatal)")


def _play_sound(sound: str) -> None:
    """Play a notification sound asynchronously."""
    if sound in {"", "none"}:
        return
    try:
        wav_path = _SOUND_MAP.get(sound)

        if sys.platform == "win32":
            import winsound
            if wav_path and wav_path.exists():
                winsound.PlaySound(
                    str(wav_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            else:
                # Fallback: Windows system asterisk sound
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)

        elif sys.platform == "darwin":
            if wav_path and wav_path.exists():
                subprocess.Popen(["afplay", str(wav_path)])
            else:
                # Fallback: macOS built-in Glass sound
                subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])

        else:
            # Linux — use aplay (ALSA utils) if available
            if wav_path and wav_path.exists():
                subprocess.Popen(["aplay", "-q", str(wav_path)])

    except Exception:
        logger.debug("Sound playback failed (non-fatal)")
