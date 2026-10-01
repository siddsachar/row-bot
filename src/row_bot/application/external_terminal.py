"""Open the person's own terminal app at a conversation's code folder.

The folder is resolved here and never taken from a page: the conversation's
bound code folder, else the home folder the in-app terminal starts in. The
app starts from an argument list with that folder as its working directory,
so no path is ever read by a shell or by a terminal's own command line, and
Row-Bot's saved keys and launch secret stay out of its environment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from row_bot.application.client_platform import ClientPlatformError

# Tried in order on Linux; each starts its shell in its working directory.
LINUX_TERMINALS = ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm")
# What Row-Bot adds to its own process for itself.
_ROW_BOT_ENVIRONMENT = frozenset({"PYTHONNOUSERSITE", "PYTHONIOENCODING"})
# Name parts that mark a credential ("OPENAI_API_KEY", "SLACK_BOT_TOKEN").
_SECRET_PARTS = frozenset({"KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
                           "CREDENTIAL", "CREDENTIALS"})


def terminal_folder(conversation_id: str | None) -> Path:
    """The conversation's code folder when one is bound, else home."""
    if conversation_id is None:
        return Path.home()
    from row_bot import threads
    from row_bot.conversation_resources import ResourceError, list_bindings
    from row_bot.developer.review import scoped_workspace_path
    from row_bot.developer.storage import get_workspace

    try:
        if not threads._thread_exists(conversation_id) or threads._thread_write_blocked(conversation_id):
            raise ClientPlatformError("not_found")
        bound = [item.resource_id for item in list_bindings(conversation_id).bindings
                 if item.kind == "workspace"]
    except ResourceError:
        raise ClientPlatformError("not_found") from None
    if not bound:
        return Path.home()
    workspace = get_workspace(bound[0])
    if workspace is None:
        raise ClientPlatformError("resource_unavailable")
    try:
        folder = scoped_workspace_path(Path(workspace.path))
        if not folder.is_dir():
            raise ValueError("resource_unavailable")
    except (OSError, ValueError):
        raise ClientPlatformError("resource_unavailable") from None
    return folder


def terminal_environment(environ: Mapping[str, str] | None = None,
                         saved: frozenset[str] | set[str] | None = None) -> dict[str, str]:
    """The person's environment without Row-Bot's own settings and secrets."""
    if saved is None:
        from row_bot.api_keys import saved_key_names

        saved = saved_key_names()
    source = os.environ if environ is None else environ
    kept: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if (upper.startswith("ROW_BOT_") or upper in _ROW_BOT_ENVIRONMENT or name in saved
                or _SECRET_PARTS.intersection(upper.replace("-", "_").split("_"))):
            continue
        kept[name] = value
    return kept


def terminal_launch(folder: Path, *, platform: str = sys.platform,
                    which: Callable[[str], str | None] = shutil.which) -> tuple[list[str], dict[str, Any]] | None:
    """Argument list and start options for the person's terminal app, if any."""
    directory = str(folder)
    if platform == "win32":
        terminal = which("wt")
        if terminal:
            # Windows Terminal opens its default profile in "." (the working
            # directory below); a path in its command line could hold ";",
            # which Windows Terminal reads as a new command.
            return [terminal, "-d", "."], {"cwd": directory}
        shell = which("cmd") or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
        return [shell], {"cwd": directory, "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)}
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if platform == "darwin":
        opener = which("open") or "/usr/bin/open"
        # The folder is an absolute path, so "open" never reads it as an option.
        return [opener, "-a", "Terminal", directory], {**quiet, "start_new_session": True}
    for name in LINUX_TERMINALS:
        found = which(name)
        if found:
            return [found], {**quiet, "cwd": directory, "start_new_session": True}
    return None


def open_terminal_at(folder: Path, *, platform: str = sys.platform,
                     which: Callable[[str], str | None] = shutil.which,
                     popen: Callable[..., Any] = subprocess.Popen,
                     environ: Mapping[str, str] | None = None) -> bool:
    """Start the person's terminal app at folder; False when none is found."""
    launch = terminal_launch(folder, platform=platform, which=which)
    if launch is None:
        return False
    argv, options = launch
    try:
        popen(argv, shell=False, close_fds=True, env=terminal_environment(environ), **options)
    except OSError:
        return False
    return True


def open_external_terminal(conversation_id: str | None, *, validate: Callable[[], None],
                           opener: Callable[[Path], bool] = open_terminal_at) -> None:
    """Open the person's terminal at the conversation's folder, or say why not."""
    validate()
    folder = terminal_folder(conversation_id)
    validate()
    if not opener(folder):
        raise ClientPlatformError("capability_unavailable")
