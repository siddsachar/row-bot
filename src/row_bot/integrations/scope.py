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


def _mcp_items(strict: bool = False) -> list[dict]:
    """Connections (standalone or in a package) with the server their chat tools come from. ``strict``:
    refuse when a source could not be read, rather than leave its apps out of the answer."""
    rows, errors = facts.inventory()
    if strict and any(error.get("source") != "skills" for error in errors):
        raise RuntimeError("apps_unreadable")
    return [item for row in rows for item in (row, *row["children"]) if item["kind"] == "mcp" and item.get("server")]


def _items(strict: bool = False) -> list[dict]:
    """Every app a chat could use: connections, and Row-Bot's built-in ways that bring chat tools
    (Google's Gmail and Calendar, X, web search)."""
    from row_bot.integrations import builtin
    return _mcp_items(strict) + [row for row in builtin.rows() if row.get("tools")]


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


def _profile(conversation_id: str) -> dict | None:
    """The chat's agent profile as a turn would freeze it: {} when it names none, None when the one it
    names is gone or switched off (a turn is refused then)."""
    from row_bot.threads import get_thread_composer_context
    try:
        context = get_thread_composer_context(conversation_id)
    except ValueError:
        return {}
    reference = context["agent_profile_id"] or context["agent_profile_slug"]
    if not reference:
        return {}
    from row_bot.agent_profiles import get_agent_profile
    return get_agent_profile(reference, enabled_only=True) or None


def _profile_allow(conversation_id: str, profile: dict | None = None) -> list[str] | None:
    """The tool ceiling of the chat's agent profile (or of ``profile``, already read), as a turn would
    freeze it (None: no limit)."""
    policy = (profile if profile is not None else _profile(conversation_id) or {}).get("tool_policy_json") or {}
    allowed = [str(v).strip() for v in (policy.get("allow_tools") or []) if str(v).strip()] if isinstance(policy, dict) else []
    return allowed or None


def _name(item: dict) -> str:
    return (item.get("app") or {}).get("name") or item["name"]


def _shown(items: list[dict]) -> list[str]:
    """How the composer names each app: its app's name, or "App (connection)" when two share one."""
    names = [_name(item) for item in items]
    return [(name if names.count(name) == 1 else f"{name} ({item['name']})")[:128] for item, name in zip(items, names)]


def _not_in_chats(row: dict) -> str:
    """Why a ready built-in way has no switch in a chat: it brings no chat tools."""
    if row["owner_ref"].startswith("channel:"):
        return f"Lets you talk to Row-Bot from {_name(row)}; nothing to switch in a chat."
    return "Used by skills and Developer, not by chat tools."


def chat_apps(conversation_id: str) -> list[dict]:
    """Every ready app, as Your apps lists them, for the composer: on unless switched off here, and
    unavailable (with why) when the chat's agent profile leaves them out. Built-in ways without chat
    tools (the GitHub account, channels) are listed without a switch, saying why."""
    from row_bot.integrations import builtin
    from row_bot.threads import get_thread_apps_off
    off, allow = set(get_thread_apps_off(conversation_id)), _profile_allow(conversation_id)
    ready = [item for item in _items() if item["lifecycle"] == "installed" and item["readiness"] == "ready"]
    found = []
    for item, name in zip(ready, _shown(ready)):
        allowed = _allowed(item, allow)
        found.append({"item_id": item["id"], "app_id": (item.get("app") or {}).get("id", ""),
                      "name": name, "icon": item["icon"], "on": item["id"] not in off, "available": allowed,
                      "switchable": True, "reason": "" if allowed else "This chat's agent profile doesn't use it."})
    for row in builtin.rows():
        if not row.get("tools") and row["lifecycle"] == "installed" and row["readiness"] == "ready":
            found.append({"item_id": row["id"], "app_id": (row.get("app") or {}).get("id", ""), "name": row["name"][:128],
                          "icon": row["icon"], "on": True, "available": True, "switchable": False,
                          "reason": _not_in_chats(row)})
    return sorted(found, key=lambda app: (not app["switchable"], app["name"].casefold(), app["item_id"]))[:64]


def _mentioned(text: str, names: dict[str, tuple[str, ...]], sigil: str) -> list[str]:
    """Which ids ``names`` (shown name -> ids) the text mentions as ``@name`` or ``/name``. The longest
    name wins where several start alike: "@Tavily (Web search)" is never also "@Tavily"."""
    found: list[str] = []
    for name in sorted(names, key=len, reverse=True):
        pattern = rf"(?<![\w{re.escape(sigil)}./]){re.escape(sigil + name.lstrip(sigil))}(?![\w-])"
        text, count = re.subn(pattern, " ", text, flags=re.IGNORECASE)
        if count:
            found.extend(item for item in names[name] if item not in found)
    return found[:MAX_FOCUS]


def _skill_names() -> dict[str, tuple[str, ...]]:
    from row_bot import slash_commands
    return {name: (spec.skill_name,) for spec in slash_commands.get_command_specs() if spec.handler_key == "activate_skill"
            for name in spec.all_names}


def turn_scope(conversation_id: str, text: str, allow: list[str] | tuple[str, ...] | None,
               previous: dict | None = None) -> dict | None:
    """What one turn leaves out: apps switched off in this chat and, when the message mentions
    apps, every other app; plus the skills it mentions, loaded for this turn only. ``previous``
    (the turn being continued) can only narrow it further. None: nothing to narrow."""
    from row_bot.threads import get_thread_apps_off
    off = set(get_thread_apps_off(conversation_id))
    items = _items()
    if off - {item["id"] for item in items}:  # An app switched off here is not in the answer: unreadable, or removed.
        items = _items(strict=True)  # Refuse rather than let an unread app back in.
    ready = [item for item in items if item["lifecycle"] == "installed" and item["readiness"] == "ready"]
    usable: dict[str, tuple[str, ...]] = {}
    for item, shown in zip(ready, _shown(ready)):  # As the composer names them; a shared app name means each.
        if item["id"] not in off and _allowed(item, allow):
            for name in {shown, _name(item)}:
                usable[name] = (*usable.get(name, ()), item["id"])
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


def step_scope(item_ids: list[str] | tuple[str, ...] | None) -> dict | None:
    """What a workflow step that names its apps leaves out: every other app, as an @mention of them
    would (and only that: the workflow's profile and every approval still apply). None: it names none."""
    wanted = list(dict.fromkeys(str(item_id) for item_id in item_ids or []))[:MAX_FOCUS]
    if not wanted:
        return None
    left_out = [item for item in _items(strict=True) if item["id"] not in wanted]  # Unreadable: refuse.
    return {"exclude_servers": sorted({item["server"] for item in left_out if item.get("server")}),
            "exclude_tools": sorted({tool for item in left_out for tool in item.get("tools") or []}),
            "focus": wanted, "skills": []}


def app_for_tool(tool_name: str) -> dict | None:
    """The app a chat tool belongs to (``{item_id, name, icon, tool}``, ``tool`` being the tool's readable
    title), from its exact runtime name; None for Row-Bot's own and for a name no app issued."""
    from row_bot.integrations import builtin
    from row_bot.mcp_client import runtime
    server = runtime.server_for_tool(tool_name) if tool_name.startswith("mcp_") else None
    if not server:
        parent = builtin.tool_parent(tool_name)
        found = builtin.tool_app(parent) if parent else None
        return {**found, "tool": ""} if found else None
    item = next((item for item in _mcp_items() if item["server"] == server), None)
    if item is None:
        return None
    from row_bot.integrations import views
    return {"item_id": item["id"], "name": _name(item)[:128], "icon": item["icon"], "tool": runtime.tool_title(tool_name),
            "view": views.has_view(tool_name, item)}


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
    from row_bot.integrations import builtin, sources
    if not isinstance(item_id, str) or not 0 < len(item_id) <= 512:
        return None
    row = facts.read(item_id) or builtin.read(item_id)  # Built in: Google for email, Telegram, web search.
    if row is None:
        found = sources.catalog_entry(item_id)
        row = found[0] if found else None
    if row is None or row["kind"] == "skill":
        return None
    return {"item_id": row["id"], "name": _name(row)[:128], "icon": row["icon"]}
