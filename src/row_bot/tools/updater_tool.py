"""Updater tool — agent-facing wrappers around updater.py.

Provides:
- ``row_bot_check_for_updates`` — read-only poll
- ``row_bot_install_update``   — performs download + install (approval-gated)
"""

from __future__ import annotations

import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry
from row_bot.tools.approval_gate import gate_action

logger = logging.getLogger(__name__)


# ── Input schemas ──────────────────────────────────────────────────────

class _NoArgs(BaseModel):
    pass


class _InstallInput(BaseModel):
    version: str = Field(
        default="",
        description=(
            "Optional version string to install (e.g. '3.18.0'). When empty, "
            "the currently-cached available update is installed."
        ),
    )


# ── Implementation ─────────────────────────────────────────────────────

def _check_for_updates() -> str:
    import row_bot.updater as updater

    try:
        info = updater.check_for_updates(force=True)
    except Exception as exc:  # pragma: no cover - defensive
        return f"Update check failed: {exc}"
    s = updater.summary_for_status()
    if s.get("dev_install"):
        return "Running from a development checkout — auto-updates are disabled."
    if info is None:
        return f"No update available. You're on v{s['current_version']} ({s['channel']})."
    lines = [
        f"Update available: v{info.version} ({info.channel} channel).",
        f"You're on v{s['current_version']}.",
    ]
    if info.notes_summary:
        lines.append("")
        lines.append(info.notes_summary)
    lines.append("")
    lines.append(f"Release page: {info.html_url}")
    lines.append("Use row_bot_install_update to install (follows this thread's approval mode).")
    return "\n".join(lines)


def _install_update(version: str = "") -> str:
    """Approval-gated install of the cached / requested update."""
    import row_bot.updater as updater

    if updater.is_dev_install():
        return "Running from a development checkout — refusing to install."

    info = updater.get_update_state().available
    if version and (info is None or info.version != version):
        # Force a re-check; user may have skipped earlier.
        info = updater.check_for_updates(force=True)
        if info is None or info.version != version:
            return f"No release matching v{version} is currently available."
    if info is None:
        info = updater.check_for_updates(force=True)
    if info is None:
        return "No update available right now."

    blocked = gate_action(
        {
            "tool": "row_bot_install_update",
            "label": f"Install Row-Bot v{info.version}",
            "description": (
                f"Download and install Row-Bot v{info.version} from "
                f"{info.html_url}. Row-Bot will close and the OS installer "
                f"will run. Asset: {info.asset_name} "
                f"({info.asset_size / 1_000_000:.1f} MB)."
            ),
            "args": {"version": info.version},
        },
        blocked_message="BLOCKED: Installing updates is disabled in Block approval mode.",
        cancelled_message="Install cancelled.",
    )
    if blocked:
        return blocked

    try:
        path = updater.download_update(info)
    except updater.UpdateError as exc:
        return f"Download failed: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("download_update raised")
        return f"Download failed: {exc}"

    try:
        updater.install_and_restart(path)
    except updater.UpdateError as exc:
        return f"Install hand-off failed: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("install_and_restart raised")
        return f"Install hand-off failed: {exc}"

    return (
        f"Installer launched for v{info.version}. Row-Bot will exit shortly; "
        f"on Windows the installer runs silently and relaunches Row-Bot, on "
        f"macOS the DMG opens in Finder for you to drag the new app to "
        f"/Applications."
    )


# ── Tool class ─────────────────────────────────────────────────────────

class UpdaterTool(BaseTool):

    @property
    def name(self) -> str:
        return "row_bot_updater"

    @property
    def display_name(self) -> str:
        return "⬆ Auto-update"

    @property
    def description(self) -> str:
        return (
            "Check for and install Row-Bot updates from GitHub Releases. "
            "Install actions follow the current thread approval mode."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def destructive_tool_names(self) -> set[str]:
        # row_bot_install_update calls interrupt() internally with its own
        # detailed approval payload — don't double-gate it.
        return set()

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_check_for_updates,
                name="row_bot_check_for_updates",
                description=(
                    "Check GitHub for a newer release of Row-Bot on the user's "
                    "channel (stable or beta). Returns the available version, "
                    "release summary, and a link to the release page. "
                    "Read-only — does not download or install anything."
                ),
                args_schema=_NoArgs,
            ),
            StructuredTool.from_function(
                func=_install_update,
                name="row_bot_install_update",
                description=(
                    "Download and install a Row-Bot update. Follows the current "
                    "thread approval mode. When allowed, Row-Bot will close and "
                    "hand off to the OS installer."
                ),
                args_schema=_InstallInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _check_for_updates()


registry.register(UpdaterTool())
