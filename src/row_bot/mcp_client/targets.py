"""Which configuration owner an MCP command addresses: standalone, or one plugin's child.

Owner functions receive the target explicitly and pass it to every
configuration read, publication and admission; nothing is ambient.
"""
from __future__ import annotations

import copy
import re


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


def admission_target(target: dict | None) -> str:
    # Child settings and package lifecycle share an exclusive target.
    return "settings:plugin:lifecycle:" + target["plugin_id"] if target else "settings:mcp"


def from_command(command: dict, target: dict | None = None) -> tuple[dict, dict | None]:
    """A wire ``payload.target`` leaves the reviewed payload and binds the admission."""
    payload = command.get("payload")
    if type(payload) is dict and "target" in payload:
        command = copy.deepcopy(command)
        target = command["payload"].pop("target")
        command["mcp_target"] = normalize(target)
    return command, normalize(target)
