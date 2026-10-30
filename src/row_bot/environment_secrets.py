"""Secrets a host hands Row-Bot in its environment, taken out as soon as they are read."""

from __future__ import annotations

import os
import sys
from typing import MutableMapping

_PR_SET_DUMPABLE = 4


def keep_environment_private() -> None:
    """On Linux, show this process's starting environment to root alone.

    ``take_secret`` keeps a secret from the programs Row-Bot starts, but the kernel still shows the
    environment a process started with at ``/proc/<pid>/environ`` to every program of the same
    user, and the agent's shell commands run as that user. A process that isn't dumpable shows it
    (and its memory) to root alone. Elsewhere this does nothing.
    """
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    if library.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Row-Bot couldn't keep its environment private")


def take_secret(name: str, environ: MutableMapping[str, str] | None = None) -> str | None:
    """The value of ``name``, removed from the environment so no program Row-Bot starts inherits it.

    Shells, MCP servers, Claude Code and every other child get a copy of the environment; a secret
    still in it would reach them all. ``None`` when it is unset or blank.
    """
    env = os.environ if environ is None else environ
    value = env.pop(name, None)
    return value if value and value.strip() else None


__all__ = ["keep_environment_private", "take_secret"]
