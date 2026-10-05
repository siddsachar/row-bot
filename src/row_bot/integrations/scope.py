"""Which apps a chat turn may use, and which app a tool belongs to.

The agent profile's tool rules are the ceiling. A chat's switches and the apps or
skills a message mentions only narrow what is left: an app that is off, not set
up, or outside the profile never gains a tool here, and a mention of one is
ignored. Nothing here changes approvals, which still apply when a tool runs.
"""
from __future__ import annotations

import re

from row_bot.integrations import facts

MAX_FOCUS = 8


def _items() -> list[dict]:
    """Every app a chat could use: connections (standalone or in a package) with their server name, and
    Row-Bot's built-in ways that bring chat tools (Google's Gmail and Calendar, X, web search)."""
    from row_bot.integrations import builtin
    rows, _ = facts.inventory()
    found = [item for row in rows for item in (row, *row["children"]) if item["kind"] == "mcp" and item.get("server")]
    return found + [row for row in builtin.rows() if row.get("tools")]


def _allowed(item: dict, allow: list[str] | tuple[str, ...] | None) -> bool:
    """Whether the agent profile's tool rules let this app's tools in at all (the ceiling)."""
    if allow is None:
        return True
    if item["kind"] == "builtin":
        return any(tool in allow for tool in item.get("tools") or [])
    from row_bot.mcp_client.safety import sanitize_name_component
    prefix = f"mcp_{sanitize_name_component(item['server'])}_"
    package = (item.get("parent_id") or "").removeprefix("plugin:")
    return "mcp" in allow or bool(package and package in allow) or any(name.startswith(prefix) for name in allow)


def _profile_allow(conversation_id: str) -> list[str] | None:
    """The tool ceiling of the chat's agent profile, as a turn would freeze it (None: no limit)."""
    from row_bot.threads import get_thread_composer_context
    try:
        context = get_thread_composer_context(conversation_id)
    except ValueError:
        return None
    reference = context["agent_profile_id"] or context["agent_profile_slug"]
    if not reference:
        return None
    from row_bot.agent_profiles import get_agent_profile
    profile = get_agent_profile(reference, enabled_only=True) or {}
    policy = profile.get("tool_policy_json") or {}
    allowed = [str(v).strip() for v in (policy.get("allow_tools") or []) if str(v).strip()] if isinstance(policy, dict) else []
    return allowed or None


def _name(item: dict) -> str:
    return (item.get("app") or {}).get("name") or item["name"]


def chat_apps(conversation_id: str) -> list[dict]:
    """The ready apps one chat can use, for the composer: on unless switched off here, and
    unavailable (with why) when the chat's agent profile leaves them out."""
    from row_bot.threads import get_thread_apps_off
    off, allow = set(get_thread_apps_off(conversation_id)), _profile_allow(conversation_id)
    ready = [item for item in _items() if item["lifecycle"] == "installed" and item["readiness"] == "ready"]
    names = [_name(item) for item in ready]
    found = []
    for item, name in zip(ready, names):
        allowed = _allowed(item, allow)
        found.append({"item_id": item["id"], "app_id": (item.get("app") or {}).get("id", ""),
                      "name": (name if names.count(name) == 1 else f"{name} ({item['name']})")[:128], "icon": item["icon"],
                      "on": item["id"] not in off, "available": allowed,
                      "reason": "" if allowed else "This chat's agent profile doesn't use it."})
    return sorted(found, key=lambda app: (app["name"].casefold(), app["item_id"]))[:64]


def _mentioned(text: str, names: dict[str, str], sigil: str) -> list[str]:
    """Which of ``names`` (shown name -> id) the text mentions as ``@name`` or ``/name``, longest first."""
    found: list[str] = []
    for name in sorted(names, key=len, reverse=True):
        pattern = rf"(?<![\w{re.escape(sigil)}./]){re.escape(sigil + name.lstrip(sigil))}(?![\w-])"
        if names[name] not in found and re.search(pattern, text, flags=re.IGNORECASE):
            found.append(names[name])
    return found[:MAX_FOCUS]


def _skill_names() -> dict[str, str]:
    from row_bot import slash_commands
    return {name: spec.skill_name for spec in slash_commands.get_command_specs() if spec.handler_key == "activate_skill"
            for name in spec.all_names}


def turn_scope(conversation_id: str, text: str, allow: list[str] | tuple[str, ...] | None,
               previous: dict | None = None) -> dict | None:
    """What one turn leaves out: apps switched off in this chat and, when the message mentions
    apps, every other app; plus the skills it mentions, loaded for this turn only. ``previous``
    (the turn being continued) can only narrow it further. None: nothing to narrow."""
    from row_bot.threads import get_thread_apps_off
    items, off = _items(), set(get_thread_apps_off(conversation_id))
    usable = {_name(item): item["id"] for item in items if item["id"] not in off and item["lifecycle"] == "installed"
              and item["readiness"] == "ready" and _allowed(item, allow)}
    focus = _mentioned(text, usable, "@") if text else []
    left_out = [item for item in items if item["id"] in off or (focus and item["id"] not in focus)]
    servers = {item["server"] for item in left_out if item.get("server")}
    parents = {tool for item in left_out for tool in item.get("tools") or []}
    skills = _mentioned(text, _skill_names(), "/") if text else []
    previous = previous or {}
    servers |= {str(name) for name in previous.get("exclude_servers") or []}
    skills = list(dict.fromkeys([*skills, *(str(name) for name in previous.get("skills") or [])]))[:MAX_FOCUS]
    tools = sorted(parents | {str(name) for name in previous.get("exclude_tools") or []})
    if not (servers or skills or tools):
        return None
    return {"exclude_servers": sorted(servers), "exclude_tools": tools, "focus": focus, "skills": skills}


def app_for_tool(tool_name: str) -> dict | None:
    """The app a chat tool belongs to (``{item_id, name, icon}``), from its runtime name; None for Row-Bot's own."""
    from row_bot.integrations import builtin
    from row_bot.mcp_client import runtime
    server = runtime.server_for_tool(tool_name) if tool_name.startswith("mcp_") else None
    if not server:
        parent = builtin.tool_parent(tool_name)
        return builtin.tool_app(parent) if parent else None
    item = next((item for item in _items() if item.get("server") == server), None)
    if item is None:
        return None
    return {"item_id": item["id"], "name": _name(item)[:128], "icon": item["icon"]}


MAX_SUGGESTIONS = 3


def suggestions(need: str) -> list[dict]:
    """Apps that could do what a chat needs, from the local catalogs only (no network): vendors and
    featured apps first, then the community. Only catalog ids come back; nothing is installed."""
    from row_bot.application.client_integrations import read_items
    need = " ".join(str(need or "").split())[:200]
    if not need:
        return []
    page = read_items(owner_id="chat-suggestions", query=need, kind="app", scope="catalog", limit=24)
    found = []
    for row in page["items"]:
        card = app_card(row["id"])
        if card is not None and row["compatibility"] != "unsupported":
            found.append(card)
        if len(found) == MAX_SUGGESTIONS:
            break
    return found


def app_card(item_id: str) -> dict | None:
    """One suggested app as a chat card shows it, re-read by id from installed items or the local
    catalogs; an id nothing local knows (or a skill) is dropped. Its name and logo come from here,
    never from the text that asked for it."""
    from row_bot.integrations import sources
    if not isinstance(item_id, str) or not 0 < len(item_id) <= 512:
        return None
    row = facts.read(item_id)
    if row is None:
        found = sources.catalog_entry(item_id)
        row = found[0] if found else None
    if row is None or row["kind"] == "skill":
        return None
    return {"item_id": row["id"], "name": _name(row)[:128], "icon": row["icon"]}
