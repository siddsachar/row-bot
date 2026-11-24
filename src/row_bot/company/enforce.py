"""The rest of the company's policy in this app (Row-Bot for Teams, C3b): tools, the approval ceiling, apps and
chat export.

Every answer comes from ``policy.current()`` (C3a): the company's policy, ``lockdown_policy()`` while locked
down, or None when no company is joined, and then nothing here changes anything. Each seam asks one question:

- **Tools.** ``tools.disabled_groups`` are registry tools (``BaseTool.name``: shell, browser, mcp…), each with
  every tool it offers; ``tools.disabled_tools`` are the model-facing names (run_command, an MCP tool's
  ``mcp_<server>_<tool>``…). A tool switched off is left out of what the model is offered (``keep_tools``,
  ``tool_off``) and a call to one that slips through is refused (``tool_refusal``).
- **The approval ceiling** (``approvals.max_level``: block < approve (Ask) < auto < full (Hands-off)). The
  person's level is capped where it takes effect (``cap``); what they stored is never rewritten, so it comes
  back when the ceiling rises or they leave. Below Auto, their standing allowances (Always allow rules, Allow
  in this chat) don't apply either (``allows_standing_approvals``): each action is asked. ``block`` refuses
  every gated action (``ceiling_blocks``, ``BLOCKED``).
- **Apps** (``apps.plugins``, ``mcp_servers``, ``skills_hub``, ``channels``: none, allow-list or any). An
  allow-list entry matches any of the names Row-Bot knows the app by (``allows_app``):
  - a plugin: its id (the manifest's, which is also its catalog id). A Custom Tool, made in Row-Bot and
    registered as a plugin (``is_custom_tool``), isn't a third-party plugin: it runs while the Custom Tool
    Builder is on;
  - an MCP server: only the catalog it was added from, as Row-Bot recorded it then (``<catalog>:<id>``, or an
    official Registry record's canonical name), never a name the person chose; one added by hand or pasted
    runs only under ``any`` (``mcp_provenance``); a server a plugin brings follows the plugin's rule;
  - a Skills Hub skill (anything added through the hub: a catalog listing, a link, an uploaded file): its
    install reference (what the hub installs from, such as ``skills_sh:owner/repo/skill``), or in a catalog
    listing its entry id; skills the person wrote in Row-Bot are not Skills Hub skills;
  - a chat channel: its id (discord, slack, sms, telegram, whatsapp, or a plugin channel's own name).
  A forbidden app can't be added and an installed one doesn't run; it is never removed, and its settings are
  kept (``APP_HELD``).
- **Export** (``data.chat_export``): a chat's export, and the full data backup, which holds every chat.
- **Share to company** (``data.share_to_company``, B4b): sending part of a chat to a company space for review.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from row_bot.company import policy as company_policy
from row_bot.policy.model import APPROVAL_LEVELS, Policy, lockdown_policy

logger = logging.getLogger(__name__)

# The company brain's registry tool (B3, ``company.brain_tool``).
COMPANY_BRAIN = "company_brain"

AppKind = Literal["plugins", "mcp_servers", "skills_hub", "channels"]
_ALLOWED = {"plugins": "allowed_plugins", "mcp_servers": "allowed_mcp_servers", "skills_hub": "allowed_skills",
            "channels": "allowed_channels"}

SET_BY_COMPANY = "Set by your company"
# The words an app the company doesn't allow shows where it would be added or run (its card, its plan).
APP_HELD = {
    "plugins": "Set by your company: your company doesn't allow this plugin.",
    "mcp_servers": "Set by your company: your company doesn't allow this MCP server.",
    "skills_hub": "Set by your company: your company doesn't allow this skill.",
    "channels": "Set by your company: your company doesn't allow this chat channel.",
}
# What the model reads when it calls a tool the company switched off (it was never offered).
TOOL_REFUSED = ("BLOCKED: {name} is switched off by your company. Do NOT retry it. Tell the person their "
                "company switched this tool off, and carry on without it.")
# What the model reads when the company's ceiling is Block.
BLOCKED = ("BLOCKED: '{label}' can't run: your company's rules don't let Row-Bot make changes right now. "
           "Do NOT retry it. Tell the person, and carry on with what doesn't make changes.")


def current() -> Policy | None:
    """The company policy in force (lockdown's while locked down), or None when no company is joined."""
    return company_policy.current()


# -- tools --------------------------------------------------------------------------------------------

def keep_tools(names: Iterable[str], policy: Policy | None = None) -> list[str]:
    """The registry tool names the company leaves on (all of them with no company, but the company brain's).

    The company brain's tool (B3) reaches the company's server: it isn't on with no company joined, nor while
    locked down to this computer."""
    policy = policy or current()
    names = list(names)
    if COMPANY_BRAIN in names and (policy is None or policy is lockdown_policy()):
        names = [name for name in names if name != COMPANY_BRAIN]
    if policy is None or not policy.tools.disabled_groups:
        return names
    return [name for name in names if name not in policy.tools.disabled_groups]


def tool_off(name: str, *, parent: str = "", policy: Policy | None = None) -> bool:
    """Whether the company switched this tool off: its model-facing ``name``, or the registry tool it came
    from (``parent``)."""
    policy = policy or current()
    if policy is None:
        return False
    return name in policy.tools.disabled_tools or bool(parent) and parent in policy.tools.disabled_groups


def tool_refusal(name: str, *, parent: str = "") -> str | None:
    """The refusal for a call to a tool the company switched off, or None."""
    if not tool_off(name, parent=parent):
        return None
    logger.info("A tool the company's policy switches off was refused")
    return TOOL_REFUSED.format(name=name)


# -- the approval ceiling -----------------------------------------------------------------------------

def ceiling(policy: Policy | None = None) -> str | None:
    """The highest approval level the company allows, or None with no company."""
    policy = policy or current()
    return None if policy is None else policy.approvals.max_level


def cap(level: str, policy: Policy | None = None) -> str:
    """``level`` (an approval level, normalised) as it runs: at most the company's ceiling."""
    highest = ceiling(policy)
    if highest is None or level not in APPROVAL_LEVELS:
        return level
    return level if APPROVAL_LEVELS.index(level) <= APPROVAL_LEVELS.index(highest) else highest


def above_ceiling(level: str, policy: Policy | None = None) -> bool:
    """Whether choosing ``level`` would go above the company's ceiling."""
    return cap(level, policy) != level


def ceiling_blocks(policy: Policy | None = None) -> bool:
    """Whether the company's ceiling is Block: every action that needs an approval is refused."""
    return ceiling(policy) == "block"


def allows_standing_approvals(policy: Policy | None = None) -> bool:
    """Whether the person's own allowances (Always allow rules, Allow in this chat) may approve for them: only
    when the company allows Auto or more, since below it each action is asked."""
    highest = ceiling(policy)
    return highest is None or APPROVAL_LEVELS.index(highest) >= APPROVAL_LEVELS.index("auto")


def blocked_words(label: str) -> str | None:
    """The model's refusal when the company's ceiling is Block, else None (the level's own words apply)."""
    return BLOCKED.format(label=label) if ceiling_blocks() else None


# -- apps ---------------------------------------------------------------------------------------------

def allows_app(kind: AppKind, *names: str, policy: Policy | None = None) -> bool:
    """Whether the company allows an app of ``kind`` known by any of ``names`` (always, with no company)."""
    policy = policy or current()
    if policy is None:
        return True
    mode = getattr(policy.apps, kind)
    if mode == "any":
        return True
    if mode == "none":
        return False
    allowed = getattr(policy.apps, _ALLOWED[kind])
    return any(name and name in allowed for name in names)


def app_refusal(kind: AppKind, *names: str, policy: Policy | None = None) -> str | None:
    """The words for an app the company doesn't allow (``APP_HELD``), or None."""
    return None if allows_app(kind, *names, policy=policy) else APP_HELD[kind]


CUSTOM_TOOL_PREFIX = "custom-tool-"


def is_custom_tool(plugin_id: str) -> bool:
    """Whether a plugin id is a Custom Tool: made in Row-Bot (a tool capsule promoted under this id) and not an
    installed or linked package that happens to use the reserved prefix."""
    if not str(plugin_id).startswith(CUSTOM_TOOL_PREFIX):
        return False
    try:
        from row_bot.developer.tool_capsules import list_promoted_capsules
        from row_bot.plugins.devtools import iter_linked_plugin_dirs
        from row_bot.plugins.installer import is_installed

        if is_installed(plugin_id) or plugin_id in iter_linked_plugin_dirs():
            return False
        return any(capsule.promoted_plugin_id == plugin_id for capsule in list_promoted_capsules())
    except Exception as error:  # noqa: BLE001 - unknown is not a Custom Tool: the plugin rule applies
        logger.info("Custom Tools couldn't be read (%s)", type(error).__name__)
        return False


def allows_plugin(plugin_id: str, policy: Policy | None = None) -> bool:
    """Whether a plugin may run. A Custom Tool (``is_custom_tool``) isn't a third-party plugin: it runs while the
    company leaves the Custom Tool Builder on."""
    policy = policy or current()
    if policy is None:
        return True
    if is_custom_tool(plugin_id):
        return "custom_tool_builder" not in policy.tools.disabled_groups
    return allows_app("plugins", plugin_id, policy=policy)


# -- MCP servers: where one came from -------------------------------------------------------------------
#
# A server counts as a catalog listing only when Row-Bot itself vouched for it as it was added: a server added
# from a catalog it checks (its own curated recipes, whose launch must be the recipe's, and the official MCP
# Registry, which the publication gate checks against the Registry's record) keeps, in its ``source``, the
# catalog names it was added as and a digest of how it launches then. Nothing pasted or typed can set them
# (``mcp_provenance`` drops them, and a plugin's marks, from what an import brings), and an edit that changes
# the launch makes the server stop matching. A server added by hand, or pasted, is known by no catalog name:
# it runs only where the company allows any MCP server. A plugin's own server is recognised only from the
# plugins' overlay, never from what a configuration says about itself.

MCP_CATALOG_NAMES = "company_catalog_names"
MCP_LAUNCH = "company_launch"
_LAUNCH_KEYS = ("transport", "command", "args", "url", "cwd", "bundle")
_VERIFIED_CATALOGS = frozenset({"curated", "official"})
_PLUGIN_KEYS = ("plugin_data", "plugin_prepared")


def mcp_launch(cfg: Mapping[str, Any]) -> str:
    """A digest of how a server launches (its transport, program, arguments, address, folder and bundle)."""
    import hashlib
    import json

    launch = {key: cfg.get(key) or None for key in _LAUNCH_KEYS}
    transport = str(launch["transport"] or ("streamable_http" if launch["url"] else "stdio"))
    launch["transport"] = {"http": "streamable_http", "streamable-http": "streamable_http"}.get(transport, transport)
    return hashlib.sha256(json.dumps(launch, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def mcp_catalog_names(catalog: str, entry_id: str, registry_name: str = "") -> tuple[str, ...]:
    """The names an allow-list may use for a listing: ``<catalog>:<id>``, and an official Registry record's
    canonical name (without a version)."""
    names = [f"{catalog}:{entry_id}"] if catalog and entry_id else []
    if catalog == "official" and registry_name:
        names.append(registry_name.split("@", 1)[0])
    return tuple(names)


def _curated_launch(entry_id: str) -> str | None:
    from row_bot.mcp_client.marketplace import CURATED_STARTER_CATALOG, entry_to_server_config

    entry = next((item for item in CURATED_STARTER_CATALOG if item.id == entry_id), None)
    return None if entry is None else mcp_launch(entry_to_server_config(entry))


def mcp_provenance(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """An imported server as it is saved: a plugin's marks and any catalog names it claims are dropped; its
    ``source`` keeps describing it. It is bound to its catalog names and launch only when it is a listing
    Row-Bot checks (``_VERIFIED_CATALOGS``)."""
    import copy

    result = copy.deepcopy(dict(cfg))
    for key in _PLUGIN_KEYS:
        result.pop(key, None)
    raw = result.get("source")
    source = {key: value for key, value in (raw.items() if isinstance(raw, Mapping) else ())
              if key not in {"kind", "plugin_id", MCP_CATALOG_NAMES, MCP_LAUNCH}}
    catalog, entry_id = str(source.get("marketplace") or ""), str(source.get("id") or "")
    launch = mcp_launch(result)
    verified = catalog in _VERIFIED_CATALOGS and bool(entry_id) and (
        catalog == "official" or _curated_launch(entry_id) == launch)
    if verified:
        source[MCP_CATALOG_NAMES] = list(mcp_catalog_names(catalog, entry_id, str(source.get("registry_name") or "")))
        source[MCP_LAUNCH] = launch
    if raw is not None or source:
        result["source"] = source
    return result


def _plugin_server(name: str, cfg: Mapping[str, Any]) -> str | None:
    """The plugin whose overlay brings this server (same name, same launch), or None."""
    try:
        from row_bot.plugins.mcp import plugin_mcp_servers

        overlay = plugin_mcp_servers().get(name)
    except Exception as error:  # noqa: BLE001 - not known to be a plugin's: the MCP rule applies
        logger.info("Plugin MCP servers couldn't be read (%s)", type(error).__name__)
        return None
    if not isinstance(overlay, Mapping) or mcp_launch(overlay) != mcp_launch(cfg):
        return None
    source = overlay.get("source")
    plugin_id = source.get("plugin_id") if isinstance(source, Mapping) else None
    return str(plugin_id) if plugin_id else None


def mcp_server_names(name: str, cfg: Mapping[str, Any] | None) -> tuple[AppKind, tuple[str, ...]]:
    """Which rule an MCP server follows, and the catalog names it is known by (none for one added by hand)."""
    cfg = cfg if isinstance(cfg, Mapping) else {}
    plugin_id = _plugin_server(str(name or ""), cfg)
    if plugin_id:
        return "plugins", (plugin_id,)
    source = cfg.get("source")
    source = source if isinstance(source, Mapping) else {}
    names = source.get(MCP_CATALOG_NAMES)
    if source.get(MCP_LAUNCH) != mcp_launch(cfg) or not isinstance(names, list):
        return "mcp_servers", ()
    return "mcp_servers", tuple(str(item) for item in names)


def mcp_refusal(name: str, cfg: Mapping[str, Any] | None, policy: Policy | None = None) -> str | None:
    """The words for an MCP server the company doesn't allow, or None."""
    policy = policy or current()
    if policy is None:
        return None
    kind, names = mcp_server_names(name, cfg)
    if kind == "plugins":
        return None if allows_plugin(names[0], policy) else APP_HELD["plugins"]
    return app_refusal(kind, *names, policy=policy)


def skill_names(local_name: str) -> tuple[str, ...] | None:
    """The names a Skills Hub skill installed as ``local_name`` is known by, or None when it isn't one."""
    from row_bot.skills_hub.provenance import get_record

    record = get_record(local_name)
    if record is None:
        return None
    return record.install_ref, str(record.metadata.get("hub_entry_ref") or "")


def skill_held(local_name: str, policy: Policy | None = None) -> bool:
    """Whether an installed skill is a Skills Hub skill the company doesn't allow."""
    policy = policy or current()
    if policy is None or policy.apps.skills_hub == "any":
        return False
    names = skill_names(local_name)
    return names is not None and not allows_app("skills_hub", *names, policy=policy)


# -- export -------------------------------------------------------------------------------------------

def allows_chat_export(policy: Policy | None = None) -> bool:
    """Whether the person may export chats, and back up their data (which holds every chat)."""
    policy = policy or current()
    return policy is None or policy.data.chat_export


def allows_share_to_company(policy: Policy | None = None) -> bool:
    """Whether the person may share to their company's spaces (never with no company joined, or locked down)."""
    policy = policy or current()
    return policy is not None and policy.data.share_to_company


# -- for the client -----------------------------------------------------------------------------------

def limits(policy: Policy | None) -> dict[str, Any] | None:
    """What the client shows as "Set by your company" (Settings › Company's view): None with no company."""
    if policy is None:
        return None

    def app(kind: AppKind) -> dict[str, Any]:
        return {"mode": getattr(policy.apps, kind), "allowed": sorted(getattr(policy.apps, _ALLOWED[kind]))}

    # Registry tools and single model-facing tools alike: Settings shows either as locked.
    return {"tools_off": sorted(policy.tools.disabled_groups | policy.tools.disabled_tools),
            "max_level": policy.approvals.max_level,
            "chat_export": policy.data.chat_export, "share_to_company": policy.data.share_to_company,
            "plugins": app("plugins"), "mcp_servers": app("mcp_servers"),
            "skills_hub": app("skills_hub"), "channels": app("channels")}


__all__ = [
    "APP_HELD", "BLOCKED", "CUSTOM_TOOL_PREFIX", "SET_BY_COMPANY", "TOOL_REFUSED", "above_ceiling",
    "allows_app", "allows_chat_export", "allows_plugin", "allows_share_to_company", "is_custom_tool", "mcp_catalog_names", "mcp_launch",
    "mcp_provenance", "MCP_CATALOG_NAMES", "MCP_LAUNCH", "allows_standing_approvals", "app_refusal", "blocked_words", "cap", "ceiling",
    "ceiling_blocks", "current", "keep_tools", "limits", "mcp_refusal", "mcp_server_names", "skill_held",
    "skill_names", "tool_off", "tool_refusal",
]
