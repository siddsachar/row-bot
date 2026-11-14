"""Row-Bot self-knowledge: identity, capabilities, and dynamic state.

Provides the self-aware context block that is injected into the agent's
system prompt so it can accurately answer questions about itself, its
features, and its current configuration.
"""

from __future__ import annotations

import logging
from typing import Optional

from row_bot.identity import get_identity_config, get_assistant_name, _DEFAULT_NAME  # get_assistant_name re-exported for convenience

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# STATIC: What I Am (sent with every chat, so short; features and how-tos are in the guides)
# ═════════════════════════════════════════════════════════════════════════════

_ABOUT_ROW_BOT_INTRO = (
    "ABOUT YOU (SELF-KNOWLEDGE):\n"
    "You are a personal AI agent that runs locally on this computer, and you are open-source.\n"
    "Your memory (a knowledge graph), conversations, workflows and settings stay in Row-Bot's\n"
    "data folder here. The person's own files are in their workspace folder, where the shell\n"
    "and file tools start: never guess its path.\n"
)

_ABOUT_ROW_BOT_OUTRO = (
    "For how to do something in Row-Bot or where a setting is, search your guides with\n"
    "row_bot_docs and answer from them in steps, each setting linked. For live state\n"
    "(model, settings, what needs attention), use row_bot_status. Don't guess.\n"
)

# ═════════════════════════════════════════════════════════════════════════════
# SKILL SELF-IMPROVEMENT GUIDANCE
# ═════════════════════════════════════════════════════════════════════════════

SKILL_CREATION_GUIDANCE = (
    "SKILL SELF-IMPROVEMENT:\n"
    "You can create and improve reusable instruction packs (skills) to get\n"
    "better at tasks you perform repeatedly.\n\n"
    "CREATING SKILLS:\n"
    "- After a successful complex workflow (5+ tool calls), consider whether\n"
    "  the pattern would benefit from a skill.\n"
    "- Ask the user before creating: 'Would you like me to save this as a\n"
    "  skill so I can do it better next time?'\n"
    "- Skills are additive — never overwrite an existing skill with the same name.\n"
    "- Use row_bot_create_skill with a clear name, description, and step-by-step\n"
    "  instructions distilled from what worked.\n\n"
    "PATCHING SKILLS:\n"
    "- If you notice a skill's instructions are incomplete or could be improved\n"
    "  based on experience, you can propose a patch.\n"
    "- Maximum 1 patch proposal per conversation — be selective.\n"
    "- Always explain what you want to change and why.\n"
    "- Use row_bot_patch_skill — it requires user confirmation and backs up\n"
    "  the original automatically.\n"
    "- Bundled skills are patched via user-space override (originals preserved).\n"
    "- Tool guides CANNOT be patched — report discrepancies as memories instead.\n"
    "- Only improve, never regress: patches must be strictly better.\n\n"
    "TROUBLESHOOTING LEARNING:\n"
    "When you diagnose an issue, discover a workflow insight, or notice a\n"
    "discrepancy between a tool guide and actual tool behavior, save it as\n"
    "a self_knowledge memory using save_memory with category='self_knowledge'.\n"
    "- Use descriptive subjects like 'Browser: login page detection' or\n"
    "  'Shell: pip install requires --user on macOS'.\n"
    "- These auto-recall on similar future queries, making you better at\n"
    "  troubleshooting over time.\n"
    "- Do NOT save trivial issues — only patterns worth remembering.\n"
    "\n"
    "SKILL INSTRUCTIONS FORMAT:\n"
    "When writing instructions for row_bot_create_skill or row_bot_patch_skill,\n"
    "follow this structure:\n"
    "- Opening paragraph: describe what the skill does and when it activates.\n"
    "- Use ## headers for major sections.\n"
    "- Use numbered lists for step-by-step workflows.\n"
    "- Use bold for key terms and constraints.\n"
    "- Reference tool names in backticks, e.g. `web_search`.\n"
    "- Keep instructions concise — the body is injected into the system prompt.\n"
)

def _build_about_row_bot() -> str:
    """Build the static self-knowledge description; features and how-tos are in Row-Bot's own guides."""
    return "\n".join([_ABOUT_ROW_BOT_INTRO.rstrip(), _ABOUT_ROW_BOT_OUTRO.rstrip()]) + "\n"


ABOUT_ROW_BOT = _build_about_row_bot()


def build_identity_line() -> str:
    """Build the dynamic identity opening line from user preferences."""
    cfg = get_identity_config()
    name = cfg.get("name") or _DEFAULT_NAME
    personality = cfg.get("personality", "")
    line = f"You are {name}, a knowledgeable personal assistant with access to tools."
    if personality:
        line += f" {personality}"
    return line


def get_dynamic_state() -> str:
    """Build a short summary of Row-Bot's current runtime state.

    Uses lazy imports to avoid circular dependencies and only queries
    state that is cheap to compute.
    """
    parts: list[str] = []

    # Current model: this turn's own (a chat's chosen model), else the default. An Opus chat was told it ran
    # on the default GPT model (R12).
    try:
        from row_bot.models import _active_model_override, get_current_model
        model = _active_model_override.get() or get_current_model()
        parts.append(f"- Current model: {model}")
    except Exception:
        pass

    # Providers configured
    try:
        from row_bot.providers.catalog import get_provider_definition
        from row_bot.providers.runtime import list_configured_provider_ids
        providers = [
            (get_provider_definition(provider_id).display_name if get_provider_definition(provider_id) else provider_id)
            for provider_id in list_configured_provider_ids()
        ]
        if providers:
            parts.append(f"- Providers configured: {', '.join(providers)}")
        else:
            parts.append("- Providers: none configured (local only)")
    except Exception:
        pass

    # The knowledge graph's size and the last Dream Cycle are left out (5.3.1 G9): they change with nearly
    # every turn, and the turn context sends this whole block again whenever any line of it changes.
    # row_bot_status reports both when they're asked for.

    # Active channels
    try:
        from row_bot.channels.registry import running_channels
        active = running_channels()
        if active:
            names = [ch.name for ch in active]
            parts.append(f"- Active channels: {', '.join(names)}")
        else:
            parts.append("- Active channels: Web UI only")
    except Exception:
        pass

    # Designer projects
    try:
        from row_bot.designer.storage import list_projects as _list_designer_projects
        _designer_count = len(_list_designer_projects())
        if _designer_count:
            parts.append(f"- Designer projects: {_designer_count}")
    except Exception:
        pass

    # Enabled skills
    try:
        from row_bot.skills import get_enabled_manual_skills
        enabled = get_enabled_manual_skills()
        if enabled:
            names = [s.display_name for s in enabled]
            parts.append(f"- Enabled skills: {', '.join(names)}")
    except Exception:
        pass

    # MCP external tool status
    try:
        from row_bot.mcp_client.runtime import get_status_summary
        mcp = get_status_summary()
        if mcp.get("enabled") or mcp.get("server_count"):
            server_bits = f"{mcp.get('connected_server_count', 0)}/{mcp.get('enabled_server_count', 0)} connected"
            parts.append(
                f"- MCP client: {'enabled' if mcp.get('enabled') else 'disabled'}, "
                f"servers {server_bits}, {mcp.get('enabled_tool_count', 0)} enabled external tools"
            )
            failed = [name for name, st in (mcp.get("servers") or {}).items() if st.get("status") in {"failed", "dependency_missing"}]
            if failed:
                parts.append(f"- MCP attention: {', '.join(failed[:5])} need troubleshooting")
    except Exception:
        pass

    # Pending Row-Bot update (auto-update)
    try:
        import row_bot.updater as updater
        info = updater.get_update_state().available
        if info is not None:
            parts.append(
                f"- Update available: v{info.version} ({info.channel} channel) — "
                f"the user can install via Settings › Updates "
                f"or by asking you to run row_bot_install_update."
            )
    except Exception:
        pass

    if not parts:
        return ""

    return "Current state:\n" + "\n".join(parts) + "\n"


def build_static_self_knowledge_block() -> str:
    """Return Row-Bot self-knowledge that is stable across normal turns."""
    from row_bot.identity import is_self_improvement_enabled

    sections = [ABOUT_ROW_BOT]
    if is_self_improvement_enabled():
        sections.append(SKILL_CREATION_GUIDANCE)
    return "\n".join(sections)


def build_dynamic_self_knowledge_block() -> str:
    """Return current Row-Bot runtime state for prompt injection."""
    state = get_dynamic_state()
    return state or ""


def build_self_knowledge_block() -> str:
    """Assemble the full self-knowledge block for prompt injection.

    Returns a string suitable for inserting as a SystemMessage.
    Omits skill-improvement guidance when self-improvement is disabled.
    """
    sections = [build_static_self_knowledge_block()]
    state = build_dynamic_self_knowledge_block()
    if state:
        sections.append(state)

    return "\n".join(sections)
