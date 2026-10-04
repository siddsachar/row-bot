"""Access presets, expressed on the existing per-tool policy (Off, Ask first, Use).

Tools are grouped by their classified effect. A tool that is destructive,
declares approval, or has an unknown effect is approval-locked: no preset lets
it run without asking, and approvals still apply at invocation. A preset
changes only the tools it is applied to; tools discovered later wait for the
user to accept them again.
"""
from __future__ import annotations

PRESETS = {
    "read_only": ("Read only", "Row-Bot can look things up but cannot change anything."),
    "ask": ("Ask before changes", "Row-Bot asks you before it changes anything."),
    "full": ("Full access", "Row-Bot makes routine changes without asking. Risky actions still ask."),
}
DEFAULT = "ask"


def locked(row: dict) -> bool:
    return bool(row.get("destructive") or row.get("requires_approval")) or row.get("effect", "unknown") == "unknown"


def tool_state(preset: str, row: dict) -> str:
    """``use``, ``ask`` or ``off`` for one tool under one preset."""
    if preset not in PRESETS:
        raise ValueError("invalid_access_preset")
    if row.get("effect") == "read_only" and not locked(row):
        return "use"
    if preset == "read_only":
        return "off"
    return "use" if preset == "full" and not locked(row) else "ask"


def apply(tools: dict, preset: str, names: list[str] | None = None) -> None:
    """Set the saved policy of the named accepted tools (all of them by default)."""
    catalog = tools.get("catalog") or {}
    enabled = tools.setdefault("enabled", {})
    approvals = set(tools.get("require_approval", []))
    for name in names if names is not None else list(tools.get("accepted_names") or catalog):
        state = tool_state(preset, catalog.get(name, {}))
        enabled[name] = state != "off"
        if state == "ask":
            approvals.add(name)
        elif state == "use":  # A tool switched off keeps its approval for when it is switched back on.
            approvals.discard(name)
        if state != "off":
            if name in tools.get("exclude", []):
                tools["exclude"] = [other for other in tools["exclude"] if other != name]
            if tools.get("include") and name not in tools["include"]:
                tools["include"] = [*tools["include"], name]
    tools["require_approval"] = sorted(approvals)


def current(tools: dict) -> str:
    """The preset the saved policy matches exactly, otherwise ``custom``."""
    catalog, enabled = tools.get("catalog") or {}, tools.get("enabled") or {}
    approvals = set(tools.get("require_approval", []))
    names = list(tools.get("accepted_names") or catalog)

    def actual(name: str) -> str:
        return "off" if not enabled.get(name, False) else "ask" if name in approvals else "use"
    return next((preset for preset in PRESETS
                 if names and all(tool_state(preset, catalog.get(name, {})) == actual(name) for name in names)), "custom")


def views() -> list[dict]:
    return [{"id": key, "label": label, "description": description, "default": key == DEFAULT}
            for key, (label, description) in PRESETS.items()]
