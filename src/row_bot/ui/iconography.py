"""Monochrome icon helpers for Row-Bot UI surfaces.

Stored workflows and older skill records may still contain emoji icon values.
Render-time mapping keeps those records readable while new UI pickers store
Material icon names.
"""

from __future__ import annotations

import re


MONO_ICON_OPTIONS: tuple[str, ...] = (
    "bolt",
    "analytics",
    "mail",
    "article",
    "search",
    "inventory_2",
    "newspaper",
    "cleaning_services",
    "lightbulb",
    "notifications",
    "event",
    "public",
    "smart_toy",
    "checklist",
    "construction",
    "target",
    "trending_up",
    "sync",
    "chat_bubble",
    "science",
    "auto_awesome",
)

ICON_LABELS: dict[str, str] = {
    "bolt": "Workflow",
    "analytics": "Analytics",
    "mail": "Mail",
    "article": "Notes",
    "search": "Search",
    "inventory_2": "Files",
    "newspaper": "News",
    "cleaning_services": "Cleanup",
    "lightbulb": "Idea",
    "notifications": "Alert",
    "event": "Schedule",
    "public": "Web",
    "smart_toy": "Agent",
    "checklist": "Checklist",
    "construction": "Tools",
    "target": "Target",
    "trending_up": "Trend",
    "sync": "Recurring",
    "chat_bubble": "Chat",
    "science": "Experiment",
    "auto_awesome": "Creative",
}

LEGACY_ICON_MAP: dict[str, str] = {
    "\u26a1": "bolt",
    "\U0001f4ca": "analytics",
    "\U0001f4e7": "mail",
    "\U0001f4dd": "article",
    "\U0001f50d": "search",
    "\U0001f5c2": "inventory_2",
    "\U0001f5c2\ufe0f": "inventory_2",
    "\U0001f4f0": "newspaper",
    "\U0001f9f9": "cleaning_services",
    "\U0001f4a1": "lightbulb",
    "\U0001f514": "notifications",
    "\U0001f4c5": "event",
    "\U0001f310": "public",
    "\U0001f916": "smart_toy",
    "\U0001f4cb": "checklist",
    "\U0001f6e0": "construction",
    "\U0001f6e0\ufe0f": "construction",
    "\U0001f3af": "target",
    "\U0001f4c8": "trending_up",
    "\U0001f504": "sync",
    "\U0001f4ac": "chat_bubble",
    "\U0001f9ea": "science",
    "\U0001f3e0": "home",
    "\U0001f44b": "waving_hand",
    "\U0001f9e0": "psychology",
    "\U0001f319": "bedtime",
    "\U0001f4e1": "sensors",
    "\U0001f3a8": "palette",
    "\U0001f3a4": "mic",
    "\U0001f4ec": "inbox",
    "\u2728": "auto_awesome",
    "\U0001f500": "alt_route",
    "\u23f8": "pause_circle",
    "\u23f8\ufe0f": "pause_circle",
    "\U0001f4e2": "campaign",
    "\u2753": "help",
    "\U0001f5fa": "map",
    "\U0001f5fa\ufe0f": "map",
    "\u2699": "settings",
    "\u2699\ufe0f": "settings",
    "\u27a1": "arrow_forward",
    "\u27a1\ufe0f": "arrow_forward",
    "\U0001f6d1": "stop_circle",
    "\U0001f4dc": "history_edu",
    "\u2705": "check_circle",
    "\u274c": "cancel",
    "\u26a0": "warning",
    "\u26a0\ufe0f": "warning",
    "\u23f3": "hourglass_empty",
    "\U0001f534": "error",
    "\U0001f9e9": "extension",
    "\U0001f3e5": "health_and_safety",
    "\u2139": "info",
    "\u2139\ufe0f": "info",
    "\U0001f4bb": "computer",
    "\U0001f5d1": "delete",
    "\U0001f5d1\ufe0f": "delete",
}

_MATERIAL_ICON_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def material_icon_for(value: object | None, fallback: str = "bolt") -> str:
    """Return a Material icon name for legacy emoji or stored icon IDs."""
    fallback_icon = fallback if _MATERIAL_ICON_RE.fullmatch(fallback or "") else "bolt"
    text = str(value or "").strip()
    if not text:
        return fallback_icon
    mapped = LEGACY_ICON_MAP.get(text)
    if mapped:
        return mapped
    if _MATERIAL_ICON_RE.fullmatch(text):
        return text
    return fallback_icon


def icon_label(icon_name: str) -> str:
    """Human label for icon picker options."""
    return ICON_LABELS.get(icon_name, icon_name.replace("_", " ").title())


def icon_select_options(
    current: object | None = None,
    *,
    fallback: str = "bolt",
) -> dict[str, str]:
    """Return picker options, including a mapped current value if needed."""
    values = list(MONO_ICON_OPTIONS)
    current_icon = material_icon_for(current, fallback=fallback)
    if current_icon and current_icon not in values:
        values.insert(0, current_icon)
    return {value: icon_label(value) for value in values}
