"""Hands-off: the approval level a person turns on, never Row-Bot.

- The first time on this install, turning it on needs the person's confirmation
  after a dialog that lists what it skips; after that it never asks again
  (``is_confirmed`` / ``confirm``).
- A conversation other people share (a Slack or Discord channel thread, a group
  chat) runs on reviewed Auto at most: Hands-off is only for the person's own
  chats (``is_shared_conversation`` / ``cap_for_conversation``).
- Agent-facing tools never set it (``AGENT_SETTABLE_MODES``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from row_bot.approval_policy import ApprovalMode, normalize_approval_mode

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
# Bump when what Hands-off skips changes enough to ask again.
NOTICE_VERSION = 1
# The levels an agent may choose for a workflow or profile it saves: never Hands-off.
AGENT_SETTABLE_MODES: tuple[ApprovalMode, ...] = ("block", "approve", "auto")

SHARED_CONVERSATION_CAP: ApprovalMode = "auto"


class FullAutonomyError(ValueError):
    """Hands-off can't be turned on here; ``code`` says why in a stable, client-safe word."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _settings_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / "approval_settings.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(settings: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(settings, handle, indent=2)
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def is_confirmed() -> bool:
    """Whether the person turned Hands-off on once on this install (no dialog again)."""

    with _LOCK:
        return int(_read().get("full_autonomy_confirmed_version") or 0) >= NOTICE_VERSION


def confirm() -> None:
    """Remember that the person read what Hands-off skips and turned it on."""

    with _LOCK:
        settings = _read()
        if int(settings.get("full_autonomy_confirmed_version") or 0) >= NOTICE_VERSION:
            return
        settings["full_autonomy_confirmed_version"] = NOTICE_VERSION
        _write(settings)
    logger.info("Hands-off confirmed on this install")


def require_choice(mode: object, *, confirmed: bool = False) -> ApprovalMode:
    """The level a person chose, checked: Hands-off needs the one-time confirmation.

    ``confirmed``: the person just turned it on in the dialog (remembered from now on).
    Raises ``FullAutonomyError("full_autonomy_unconfirmed")`` when it was never confirmed.
    """

    normalized = normalize_approval_mode(mode)
    if normalized != "full":
        return normalized
    if confirmed:
        confirm()
    elif not is_confirmed():
        raise FullAutonomyError("full_autonomy_unconfirmed")
    return normalized


def is_shared_conversation(thread_id: str) -> bool:
    """Whether other people share this conversation (a channel thread or group chat).

    Only channel conversations can be shared; Row-Bot's own chats never are.
    """

    clean = str(thread_id or "").strip()
    if not clean:
        return False
    try:
        from row_bot.tasks import get_thread_channel_ref

        ref = get_thread_channel_ref(clean)
    except Exception:
        logger.debug("Channel origin of %s unavailable", clean[:8], exc_info=True)
        return False
    return bool(ref and int(ref.get("shared") or 0))


def cap_for_conversation(thread_id: str, mode: object) -> ApprovalMode:
    """The level a conversation runs at: Hands-off becomes Auto where others share it."""

    normalized = normalize_approval_mode(mode)
    if normalized == "full" and is_shared_conversation(thread_id):
        return SHARED_CONVERSATION_CAP
    return normalized


def agent_settable(mode: object) -> ApprovalMode:
    """A level an agent asked for, for a workflow or profile it saves: Hands-off is refused."""

    normalized = normalize_approval_mode(mode)
    if normalized not in AGENT_SETTABLE_MODES:
        raise FullAutonomyError("full_autonomy_person_only")
    return normalized
