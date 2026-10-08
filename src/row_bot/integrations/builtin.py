"""Built-in ways to connect: Row-Bot's own accounts, channels and key-based tools, in Apps.

Their owners stay authoritative. This only reads what they already know (saved files, whether a
key is in the keychain, the channel registry and the last check), never contacts a service, starts
no program (not even the GitHub CLI) and changes nothing. Each one opens its owner's page scoped to
it, so there is one place to edit it.
Credentials stay where each owner keeps them: an account's token is never another app's key.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
import json
import logging
import sys
import threading
import time

from row_bot import secret_store
from row_bot.integrations import apps, facts

logger = logging.getLogger(__name__)
MAX_AGE = 60.0  # A snapshot older than this is still served, and refreshed in the background.

# Account id -> (name, the chat tools it powers).
ACCOUNTS = {"github": ("GitHub account", ()), "google": ("Google account", ("gmail", "calendar")),
            "x": ("X account", ("x",))}
# Key-based tools: tool id -> (name, keychain key names, what it does).
TOOLS = {"web_search": ("Web search", ("TAVILY_API_KEY",), "Search the web with your own Tavily key."),
         "wolfram_alpha": ("Wolfram Alpha", ("WOLFRAM_ALPHA_APPID",), "Maths, science and data answers with your own Wolfram Alpha key.")}
_ACCOUNT_STATES = {"connected": None, "saved_unchecked": None, "configured_unchecked": None, "anonymous": None,
                   "rate_limited": None, "secondary_limited": None, "offline": None, "not_authenticated": "sign_in_required",
                   "partial": "sign_in_required", "invalid": "expired", "expired": "expired", "invalid_token": "expired",
                   "unavailable": "connection_failed"}


def _row(ref: str, name: str, *, app_ref: str, lifecycle: str, blockers: list[dict] = (), description: str = "",
         tools: tuple[str, ...] = ()) -> dict:
    app = apps.match([app_ref])
    # Part of Row-Bot, not published by the service: it reads "Built in", never "by Google".
    row = facts.entry("builtin", ref, name, app=app.ref() if app else None, source="builtin",
                      publisher="Row-Bot", description=description, installed=lifecycle != "available",
                      enabled=lifecycle == "installed", lifecycle=lifecycle, blockers=list(blockers),
                      canonical_identity="builtin:" + ref, compatibility="supported", evidence_stage="inspected")
    row["tools"] = list(tools)  # The chat tools this way brings, for app identity and chat switches.
    facts.finish(row)
    if not tools and row["next_action"]["kind"] == "try":
        row["next_action"] = {"kind": "none", "label": ""}  # Nothing to try in chat: it serves skills or a channel.
    return row


def _tool_on(tool: str) -> bool:
    """Whether a tool is on: from the loaded tool registry, else its saved switch (a read never loads tools)."""
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is not None and registry.get_tool(tool) is not None:
        return bool(registry.is_enabled(tool))
    from row_bot.data_paths import get_row_bot_data_dir
    try:
        saved = json.loads((get_row_bot_data_dir(create=False) / "tools_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    tools = saved.get("tools") if isinstance(saved.get("tools"), dict) else saved
    value = tools.get(tool) if isinstance(tools, dict) else None
    return value if isinstance(value, bool) else True


def _accounts(only: str = "") -> list[dict]:
    from row_bot.application.client_account_oauth import read_account_auth
    found = []
    for account, (name, tools) in ACCOUNTS.items():
        if only and account != only:
            continue
        unchecked_cli = ""
        try:
            if account == "github":
                from row_bot import github_account
                # Never starts the GitHub CLI: its sign-in is known only from the last check that asked it.
                status = github_account.shared_github_status()
                state = status.state
                if state == "configured_unchecked" and getattr(status, "source", "") == "github_cli":
                    unchecked_cli = status.message  # "Check GitHub", not a guess either way.
            else:
                state = read_account_auth(account=account)["state"]
        except Exception:
            logger.debug("Account %s state unavailable", account, exc_info=True)
            state = "unavailable"
        if state == "not_configured":
            lifecycle, blockers = "available", []
        else:
            code = "not_connected" if unchecked_cli else _ACCOUNT_STATES.get(state, "connection_failed")
            blockers = [facts.blocker(code, unchecked_cli)] if code else []
            on = not tools or any(_tool_on(tool) for tool in tools)
            lifecycle = "installed" if on else "off"
        found.append(_row("account:" + account, name, app_ref="account:" + account, lifecycle=lifecycle, blockers=blockers,
                          tools=tools, description={"github": "Lets Row-Bot work with GitHub for skills and Developer.",
                                                    "google": "Gmail and Calendar through your own Google sign-in.",
                                                    "x": "Read and post on X through your own app."}[account]))
    return found


def _channels() -> list[dict]:
    from row_bot.application.channel_controls import read_channels
    found = []
    try:
        page = read_channels(limit=50)
    except Exception:
        logger.debug("Channel states unavailable", exc_info=True)
        return []
    for item in page["items"]:
        source = item.get("source") or {}
        if source.get("kind") == "plugin":
            continue  # A package's channel is listed with its package.
        problem = item.get("reachability_problem")
        # Set up by the person: a saved setting, a linked or paired account, or running. A channel that needs
        # nothing saved (WhatsApp links by scanning) reads "configured" before anyone has set it up.
        used = (item.get("running") is True or item.get("link_state") == "linked" or bool(item.get("paired_identities"))
                or any(field.get("configured") is True for field in item.get("fields") or []))
        if item.get("configured") is not True or not used:
            lifecycle, blockers = "available", []
        else:
            lifecycle = "installed" if item.get("running") is True else "off"
            blockers = [facts.blocker("connection_failed", str(problem)[:512])] if problem and lifecycle == "installed" else []
        found.append(_row("channel:" + item["channel_id"], item["display_name"] + " channel", lifecycle=lifecycle,
                          app_ref="channel:" + item["channel_id"], blockers=blockers,
                          description=f"Talk to Row-Bot from {item['display_name']}."))
    return found


def _tools(only: str = "") -> list[dict]:
    from row_bot.api_keys import key_status
    found = []
    for tool, (name, keys, description) in TOOLS.items():
        if only and tool != only:
            continue
        try:
            saved = all(key_status(key).get("configured") is True for key in keys)
        except Exception:
            saved = False
        lifecycle = "available" if not saved else "installed" if _tool_on(tool) else "off"
        found.append(_row("tool:" + tool, name, app_ref="tool:" + tool, lifecycle=lifecycle, tools=(tool,),
                          description=description))
    return found


# One snapshot of every way, so list reads (Apps, the catalog, the composer, a turn's scope) never open the
# keychain, read an account's saved sign-in or ask the GitHub CLI. Owners call ``changed()`` when they change.
_built = threading.Lock()  # One build at a time; others wait for it rather than read the owners again.
_snapshot: dict = {}
_generation = 0


def _now_generation() -> tuple[int, int]:
    """This module's own count of owner changes, and the keychain's count of saved and removed secrets."""
    return _generation, secret_store.change_count()


def changed(*_: object) -> None:
    """An owner changed something a way reads (a key, a sign-in, a channel, a tool switch): the next
    read builds the snapshot again."""
    global _generation
    _generation += 1


def _key() -> str:
    from row_bot.data_paths import get_row_bot_data_dir
    return str(get_row_bot_data_dir(create=False))


def _build(key: str) -> list[dict]:
    with _built:
        generation = _now_generation()
        if (_snapshot.get("key") == key and _snapshot.get("generation") == generation
                and time.monotonic() - _snapshot["at"] <= MAX_AGE):
            return _snapshot["rows"]  # Another read built it while this one waited.
        found = [*_accounts(), *_channels(), *_tools()]
        _snapshot.update(key=key, generation=generation, at=time.monotonic(), rows=found)
        return found


def _refresh(key: str) -> None:
    if not _built.locked():
        threading.Thread(target=lambda: _build(key) if _key() == key else None, daemon=True,
                         name="builtin-ways-refresh").start()


def rows(validate: Callable[[], None] = lambda: None, *, wait: bool = True) -> list[dict]:
    """Every built-in way to connect, with its status from its owner as of the last snapshot. ``wait=False``
    (catalog search) never waits for the owners: before the first snapshot it lists the ways without status."""
    validate()
    key = _key()
    current = _snapshot if _snapshot.get("key") == key else {}
    if current and current["generation"] == _now_generation():
        if time.monotonic() - current["at"] > MAX_AGE:
            _refresh(key)
        found = current["rows"]
    elif not wait:
        _refresh(key)
        found = current.get("rows") or _unread()
    else:
        found = _build(key)
    validate()
    return copy.deepcopy(found)


def _unread() -> list[dict]:
    """The accounts and key tools as ways, before their owners have been read (channels need the registry)."""
    return [*(_row("account:" + account, name, app_ref="account:" + account, lifecycle="available", tools=tools)
              for account, (name, tools) in ACCOUNTS.items()),
            *(_row("tool:" + tool, name, app_ref="tool:" + tool, lifecycle="available", tools=(tool,), description=description)
              for tool, (name, _, description) in TOOLS.items())]


def read(item_id: str, validate: Callable[[], None] = lambda: None) -> dict | None:
    """One built-in way by its id (``builtin:account:google``), or None."""
    kind, _, ref = item_id.removeprefix("builtin:").partition(":")
    if not item_id.startswith("builtin:") or not ref:
        return None
    validate()  # Only the owner the id names is read.
    found = {"account": lambda: _accounts(only=ref), "channel": _channels, "tool": lambda: _tools(only=ref)}.get(kind)
    row = next((row for row in found() if row["id"] == item_id), None) if found else None
    validate()
    return row


def app_ways(app: apps.App) -> list[dict]:
    """The built-in ways an app has (its account, channel or tool), from the app's reviewed references."""
    refs = {ref for ref in app.refs if ref.split(":", 1)[0] in {"account", "channel", "tool"}}
    return [row for row in rows() if row["owner_ref"] in refs] if refs else []


def tool_app(parent: str) -> dict | None:
    """The built-in way whose chat tools include this tool parent (``gmail`` -> Google)."""
    for account, (name, tools) in ACCOUNTS.items():
        if parent in tools:
            return _identity("account:" + account, name)
    if parent in TOOLS:
        return _identity("tool:" + parent, TOOLS[parent][0])
    return None


_PARENTS: dict[str, str] = {}  # A chat tool's runtime name -> its built-in tool, as the agent bound it.


def _parents() -> list[str]:
    return [tool for _, tools in ACCOUNTS.values() for tool in tools] + list(TOOLS)


def remember_tools(bound: Mapping[str, str]) -> None:
    """Note which built-in tool each bound chat tool came from (runtime name -> tool), when a turn binds
    its tools. Building a tool can sign in or check a token over the network, so a read never does."""
    parents = set(_parents())
    _PARENTS.update({str(name): str(parent) for name, parent in bound.items() if parent in parents and name})


def tool_parent(name: str) -> str | None:
    """Which built-in way's tool a chat tool's runtime name belongs to (``send_gmail_message`` -> gmail)."""
    return name if name in _parents() else _PARENTS.get(name)


def _identity(ref: str, name: str) -> dict:
    app = apps.match([ref])
    return {"item_id": "builtin:" + ref, "name": (app.name if app else name)[:128],
            "icon": (app.icon or apps.letter(app.name)) if app else apps.letter(name)}


secret_store.on_change(changed)  # A saved or removed key, token or channel secret.
