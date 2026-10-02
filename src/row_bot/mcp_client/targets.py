"""A scoped choice of the existing standalone or plugin configuration owner.

The scope is carried into launch validation callbacks, never into the global
runtime configuration. It lets policy, catalog and publication retain one path.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import copy
import re

_TARGET: ContextVar[dict | None] = ContextVar("mcp_target", default=None)


def current() -> dict | None:
    return _TARGET.get()


def normalize(value: dict | None) -> dict | None:
    if value is None or value == {"kind": "standalone"}:
        return None
    if (type(value) is not dict or set(value) != {"kind", "plugin_id", "server_key"}
            or value["kind"] != "plugin"
            or type(value["plugin_id"]) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", value["plugin_id"])
            or type(value["server_key"]) is not str or not 1 <= len(value["server_key"]) <= 256
            or any(ord(c) < 32 for c in value["server_key"])):
        raise ValueError("invalid_mcp_target")
    return dict(value)


@contextmanager
def scope(target: dict | None):
    token = _TARGET.set(normalize(target))
    try:
        yield
    finally:
        _TARGET.reset(token)


def admission_target() -> str:
    target = current()
    # Child settings and package lifecycle share an exclusive target.
    return "settings:plugin:lifecycle:" + target["plugin_id"] if target else "settings:mcp"


def bind(callback):
    target = current()
    @wraps(callback)
    def bound(*args, **kwargs):
        with scope(target):
            return callback(*args, **kwargs)
    return bound


def owner(function):
    """Accept an optional typed target on existing public MCP owner functions."""
    @wraps(function)
    def targeted(*args, **kwargs):
        target = kwargs.pop("target", current())
        command = kwargs.get("command")
        if command is not None and "target" in command.get("payload", {}):
            command = copy.deepcopy(command)
            target = command["payload"].pop("target")
            command["mcp_target"] = normalize(target)
            kwargs["command"] = command
        with scope(target):
            for key in ("validate", "validate_review"):
                if key in kwargs:
                    kwargs[key] = bind(kwargs[key])
            return function(*args, **kwargs)
    return targeted
