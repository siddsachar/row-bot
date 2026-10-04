"""Access presets, expressed on the existing per-tool policy (Off, Ask first, Use).

Tools are grouped by their classified effect. A tool that is destructive or high
impact, or has an unknown effect, is approval-locked: no preset or override lets it
run without asking, and approvals still apply at invocation. A routine change runs
without asking only when the user allowed it (Full access, or "Use" for that tool),
which is recorded in ``run_without_asking``. A preset changes only the tools it is
applied to; tools discovered later wait for the user to accept them again.
"""
from __future__ import annotations

PRESETS = {
    "read_only": ("Read only", "Row-Bot can look things up but cannot change anything."),
    "ask": ("Ask before changes", "Row-Bot asks you before it changes anything."),
    "full": ("Full access", "Row-Bot makes routine changes without asking. Risky actions still ask."),
}
DEFAULT = "ask"
STATES = ("use", "ask", "off")


def locked(row: dict) -> bool:
    """Destructive, high impact, unknown, or declared to need approval: it asks in every preset.

    A routine change's ``requires_approval`` only reflects that it has not been allowed yet."""
    effect = row.get("effect", "unknown")
    return bool(row.get("destructive")) or effect == "unknown" or (bool(row.get("requires_approval")) and effect != "mutation")


def tool_state(preset: str, row: dict) -> str:
    """``use``, ``ask`` or ``off`` for one tool under one preset."""
    if preset not in PRESETS:
        raise ValueError("invalid_access_preset")
    if row.get("effect") == "read_only" and not locked(row):
        return "use"
    if preset == "read_only":
        return "off"
    return "use" if preset == "full" and not locked(row) else "ask"


def set_state(tools: dict, name: str, state: str, row: dict) -> None:
    """Save one tool's state; a locked tool can never be set to run without asking."""
    if state not in STATES or (state == "use" and locked(row)):
        raise ValueError("approval_required")
    tools.setdefault("enabled", {})[name] = state != "off"
    approvals, allowed = set(tools.get("require_approval", [])), set(tools.get("run_without_asking", []))
    allowed.discard(name)
    if state == "ask":
        approvals.add(name)
    elif state == "use":  # A tool switched off keeps its approval for when it is switched back on.
        approvals.discard(name)
        if row.get("effect") == "mutation":
            allowed.add(name)
    if state != "off":
        if name in tools.get("exclude", []):
            tools["exclude"] = [other for other in tools["exclude"] if other != name]
        if tools.get("include") and name not in tools["include"]:
            tools["include"] = [*tools["include"], name]
    tools["require_approval"], tools["run_without_asking"] = sorted(approvals), sorted(allowed)


def apply(tools: dict, preset: str, names: list[str] | None = None, overrides: dict | None = None) -> None:
    """Set the saved policy of the named accepted tools (all of them by default), then the user's overrides."""
    catalog = tools.get("catalog") or {}
    for name in names if names is not None else list(tools.get("accepted_names") or catalog):
        row = catalog.get(name, {})
        set_state(tools, name, (overrides or {}).get(name) or tool_state(preset, row), row)


def actual(tools: dict, name: str) -> str:
    """What one saved tool does now, matching the invocation gate."""
    row = (tools.get("catalog") or {}).get(name, {})
    if not (tools.get("enabled") or {}).get(name, False):
        return "off"
    asks = name in tools.get("require_approval", []) or locked(row) or (
        row.get("effect") == "mutation" and name not in tools.get("run_without_asking", []))
    return "ask" if asks else "use"


def current(tools: dict) -> str:
    """The preset the saved policy matches exactly, otherwise ``custom``."""
    catalog = tools.get("catalog") or {}
    names = list(tools.get("accepted_names") or catalog)
    return next((preset for preset in PRESETS
                 if names and all(tool_state(preset, catalog.get(name, {})) == actual(tools, name) for name in names)), "custom")


def views() -> list[dict]:
    return [{"id": key, "label": label, "description": description, "default": key == DEFAULT}
            for key, (label, description) in PRESETS.items()]
