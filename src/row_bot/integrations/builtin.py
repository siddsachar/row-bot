"""Built-in ways to connect: Row-Bot's own accounts, channels and key-based tools, in Apps.

Their owners stay authoritative. This only reads what they already know (saved files, whether a
key is in the keychain, the channel registry and the last check), never contacts a service and
changes nothing. Each one opens its owner's page scoped to it, so there is one place to edit it.
Credentials stay where each owner keeps them: an account's token is never another app's key.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
import sys

from row_bot.integrations import apps, facts

logger = logging.getLogger(__name__)

# Account id -> (name, the chat tools it powers).
ACCOUNTS = {"github": ("GitHub account", ()), "google": ("Google account", ("gmail", "calendar")),
            "x": ("X account", ("x",))}
# Key-based tools: tool id -> (name, keychain key names).
TOOLS = {"web_search": ("Web search", ("TAVILY_API_KEY",)), "wolfram_alpha": ("Wolfram Alpha", ("WOLFRAM_ALPHA_APPID",))}
_ACCOUNT_STATES = {"connected": None, "saved_unchecked": None, "configured_unchecked": None, "anonymous": None,
                   "rate_limited": None, "secondary_limited": None, "offline": None, "not_authenticated": "sign_in_required",
                   "partial": "sign_in_required", "invalid": "expired", "expired": "expired", "invalid_token": "expired",
                   "unavailable": "connection_failed"}


def _row(ref: str, name: str, *, app_ref: str, lifecycle: str, blockers: list[dict] = (), description: str = "",
         publisher: str = "Row-Bot", tools: tuple[str, ...] = (), parent_id: str | None = None) -> dict:
    app = apps.match([app_ref])
    # Part of Row-Bot, not published by the service: it reads "Built in", never "by Google".
    row = facts.entry("builtin", ref, name, app=app.ref() if app else None, source="builtin",
                      publisher=publisher, description=description, installed=lifecycle != "available",
                      enabled=lifecycle == "installed", lifecycle=lifecycle, blockers=list(blockers), parent_id=parent_id,
                      canonical_identity="builtin:" + ref, compatibility="supported", evidence_stage="inspected",
                      evidence="Built into Row-Bot.")
    row["tools"] = list(tools)  # The chat tools this way brings, for app identity and chat switches.
    return facts.finish(row)


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


def _accounts(chat_tools: bool = False, only: str = "") -> list[dict]:
    from row_bot.application.client_account_oauth import read_account_auth
    found = []
    for account, (name, tools) in ACCOUNTS.items():
        if (chat_tools and not tools) or (only and account != only):
            continue
        try:
            if account == "github":
                from row_bot import github_account
                state = github_account.shared_github_status().state
            else:
                state = read_account_auth(account=account)["state"]
        except Exception:
            logger.debug("Account %s state unavailable", account, exc_info=True)
            state = "unavailable"
        if state == "not_configured":
            lifecycle, blockers = "available", []
        else:
            code = _ACCOUNT_STATES.get(state, "connection_failed")
            blockers = [facts.blocker(code)] if code else []
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
    for tool, (name, keys) in TOOLS.items():
        if only and tool != only:
            continue
        try:
            saved = all(key_status(key).get("configured") is True for key in keys)
        except Exception:
            saved = False
        lifecycle = "available" if not saved else "installed" if _tool_on(tool) else "off"
        found.append(_row("tool:" + tool, name, app_ref="tool:" + tool, lifecycle=lifecycle, tools=(tool,),
                          description="A Row-Bot tool that uses your own key, kept in your system keychain."))
    return found


def rows(validate: Callable[[], None] = lambda: None, *, chat_tools: bool = False) -> list[dict]:
    """Every built-in way to connect, with its status from its owner. ``chat_tools``: only those that bring
    chat tools (Google, X, key tools), read without the GitHub CLI or the channel registry."""
    validate()
    found = [*_accounts(chat_tools), *_tools()] if chat_tools else [*_accounts(), *_channels(), *_tools()]
    validate()
    return found


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
    for account, (_, tools) in ACCOUNTS.items():
        if parent in tools:
            return _identity("account:" + account, ACCOUNTS[account][0])
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
