"""Secrets a host hands Row-Bot in its environment, taken out as soon as they are read."""

from __future__ import annotations

import os
from typing import MutableMapping


def take_secret(name: str, environ: MutableMapping[str, str] | None = None) -> str | None:
    """The value of ``name``, removed from the environment so no program Row-Bot starts inherits it.

    Shells, MCP servers, Claude Code and every other child get a copy of the environment; a secret
    still in it would reach them all. ``None`` when it is unset or blank.
    """
    env = os.environ if environ is None else environ
    value = env.pop(name, None)
    return value if value and value.strip() else None


__all__ = ["take_secret"]
