"""Claude Code kept up to date: once a day, while a Claude plan is set up.

Row-Bot runs Claude Code with its own automatic updates off (``claude_code_environment``), so it chooses when
Claude Code changes. About once a day, and only while Claude is set up (``claude_plan_status`` is ready or too
old), it takes the steps "Update Claude Code" takes (``claude_install.py``): it reads the newest version from
Anthropic's release location and, only when that is newer than the installed one, downloads it, checks it
against the SHA-256 in Anthropic's manifest and runs the binary's own ``install latest``.

The program is replaced only while no Claude Code run is live, and no run starts while it is replaced
(``ClaudeCodeRuns``); a check that finds Claude working waits for the next hourly look. After an update, Claude's
model list is read again, so new models appear. A failure keeps the installed Claude Code and logs a short
reason; the next attempt is the next day. Only a Claude Code where Anthropic's installer puts it
(``~/.local/bin``) is updated: one from npm or Homebrew is updated through those.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from row_bot.providers.claude_subscription import (
    CLAUDE_SUBSCRIPTION_PROVIDER_ID,
    claude_code_runs,
    claude_plan_status,
)

logger = logging.getLogger(__name__)

#: How often Claude Code is checked: the last check's time is kept, so restarts don't check again.
CHECK_EVERY = timedelta(days=1)
#: How often the scheduler looks whether a check is due (and whether Claude is free for one).
_LOOK_EVERY = timedelta(hours=1)
_FIRST_LOOK = timedelta(minutes=15)
#: How long a downloaded update waits for Claude's runs to end before it is put off to the next check.
IDLE_WAIT_SECONDS = 300.0
_CHECKED_AT = "claude_code_checked_at"
JOB_ID = "claude_code_daily_update"

Outcome = Literal["no_plan", "not_due", "busy", "elsewhere", "current", "updated", "deferred", "failed"]


def native_claude_code() -> Path:
    """Where Anthropic's installer (and so ``install latest``) puts ``claude``."""
    return Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")


def _same_file(a: str | Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _checked_at() -> datetime | None:
    from row_bot.providers.config import load_provider_config

    entry = load_provider_config().get("providers", {}).get(CLAUDE_SUBSCRIPTION_PROVIDER_ID, {})
    try:
        when = datetime.fromisoformat(str(entry.get(_CHECKED_AT) or "")) if isinstance(entry, dict) else None
    except ValueError:
        return None
    return when if when is None or when.tzinfo else when.replace(tzinfo=timezone.utc)


def _record_check(now: datetime) -> None:
    from row_bot.providers.config import update_provider_config

    def _update(cfg: dict[str, Any]) -> None:
        entry = cfg.setdefault("providers", {}).setdefault(CLAUDE_SUBSCRIPTION_PROVIDER_ID, {})
        entry["provider_id"] = CLAUDE_SUBSCRIPTION_PROVIDER_ID
        entry[_CHECKED_AT] = now.isoformat()

    update_provider_config(_update)


def update_claude_code() -> Outcome:
    """One look: check for (and install) a newer Claude Code when a check is due and Claude is free."""
    from row_bot.providers.claude_install import claude_code_installer

    status = claude_plan_status()
    if status.state not in {"ready", "too_old"}:
        return "no_plan"
    now = datetime.now(timezone.utc)
    last = _checked_at()
    if last is not None and now - last < CHECK_EVERY:
        return "not_due"
    if not _same_file(status.program, native_claude_code()):
        logger.info("Claude Code isn't where Anthropic's installer puts it, so it updates the way it was installed")
        _record_check(now)
        return "elsewhere"
    runs = claude_code_runs()
    if runs.busy():
        return "busy"
    installer = claude_code_installer()
    outcome = installer.update_if_newer(status.version, replace=lambda: runs.replacing(IDLE_WAIT_SECONDS))
    if outcome is None:  # the person's own install or update is running
        return "busy"
    if outcome == "deferred":  # a run started during the download and didn't end: look again next hour
        logger.info("Claude Code's update waited for Claude to finish; it tries again later")
        return "deferred"
    _record_check(now)
    if outcome == "failed":
        logger.info("Claude Code %s kept: its update failed (%s)", status.version, installer.state().reason)
        return "failed"
    if outcome == "current":
        return "current"
    from row_bot.providers.claude_subscription import forget_claude_plan_status
    from row_bot.providers.model_catalog_cache import start_model_catalog_refresh_background

    forget_claude_plan_status()
    start_model_catalog_refresh_background(reason="claude_code_updated", provider_id=CLAUDE_SUBSCRIPTION_PROVIDER_ID,
                                           force=True, follow_up=True)
    return "updated"


def _look() -> None:
    try:
        update_claude_code()
    except Exception:
        logger.warning("Claude Code's daily update check stopped", exc_info=True)


def schedule(scheduler: Any) -> None:
    """At start-up: only the job. It first looks a few minutes later, then hourly; a check runs once a day."""
    scheduler.add_job(_look, trigger="interval", seconds=int(_LOOK_EVERY.total_seconds()), id=JOB_ID,
                      replace_existing=True, coalesce=True, max_instances=1,
                      next_run_time=datetime.now() + _FIRST_LOOK)
