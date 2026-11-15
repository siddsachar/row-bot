"""Row-Bot's self tools: what it is now, its own guides, its settings and what needs attention.

- ``row_bot_status`` reads live state one area at a time (no approval: it changes nothing).
- ``row_bot_docs`` searches Row-Bot's own guides, shipped with this version and read offline (``row_bot.guides``).
- ``row_bot_update_setting`` changes one of the settings in ``row_bot.self_settings``: checked first, approved
  by the chat's approval level, undoable.
- ``row_bot_fix`` checks again or hands the person a problem's fix (``row_bot.self_health``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)

_DATA_DIR = get_row_bot_data_dir()


# ═════════════════════════════════════════════════════════════════════════════
# INPUT SCHEMAS
# ═════════════════════════════════════════════════════════════════════════════

def _setting_ids() -> tuple[str, ...]:
    from row_bot.self_settings import IDS

    return IDS


class _SettingUpdateInput(BaseModel):
    setting: Literal[_setting_ids()] = Field(  # type: ignore[valid-type]
        description="The setting to change; row_bot_status 'settings' lists each with what it takes."
    )
    value: str = Field(description="The new value, in the form row_bot_status 'settings' gives for it.")


def _pinned_choice_status_lines(
    surface: str,
    heading: str,
    *,
    value_label: str = "Canonical ref",
    value_key: str = "canonical_ref",
    limit: int | None = 8,
) -> list[str]:
    try:
        from row_bot.providers.selection import pinned_model_choice_summaries

        choices = pinned_model_choice_summaries(surface, include_inactive=True)
    except Exception as exc:
        return ["", f"**{heading}**", f"- Unavailable: {exc}"]

    lines = ["", f"**{heading}**"]
    if not choices:
        lines.append("- None pinned. Pin choices in Settings › Models.")
        return lines

    active = [choice for choice in choices if choice.get("active") is not False]
    inactive = [choice for choice in choices if choice.get("active") is False]
    if surface == "chat":
        lines.append(
            "- For natural Brain model requests, choose from this list and call tools with the canonical ref."
        )
    shown_active = active if not limit or limit < 0 else active[:limit]
    for choice in shown_active:
        value = str(choice.get(value_key) or choice.get("canonical_ref") or "")
        lines.append(
            f"- {choice.get('display_name')} - {choice.get('provider')}: "
            f"{value_label}: {value} (provider_id: {choice.get('provider_id')}, model_id: {choice.get('model_id')})"
        )
    hidden_count = len(active) - len(shown_active)
    if hidden_count > 0:
        lines.append(f"- Plus {hidden_count} more active pinned choice(s).")
    if inactive:
        lines.append(f"- Not usable now: {len(inactive)} pinned choice(s)")
        for choice in inactive[:3]:
            value = str(choice.get(value_key) or choice.get("canonical_ref") or "")
            reason = str(choice.get("reason") or "inactive")
            lines.append(
                f"  - {choice.get('display_name')} - {choice.get('provider')}: "
                f"{value_label}: {value} ({reason})"
            )
    return lines


def _tool_display_label(tool) -> str:
    from row_bot.self_settings import tool_display_label

    return tool_display_label(tool)


# ═════════════════════════════════════════════════════════════════════════════
# QUERY HANDLERS (read-only, always allowed)
# ═════════════════════════════════════════════════════════════════════════════

def _query_overview() -> str:
    """A short summary: version, the model and whether it can use tools, approval level, problems, update."""
    from row_bot.approval_policy import approval_label
    from row_bot.version import __version__

    lines = [f"**Row-Bot v{__version__}**", _model_summary_line()]
    try:
        from row_bot.agent import get_approval_mode

        lines.append(f"- Approval level for this chat: {approval_label(get_approval_mode())}")
    except Exception as exc:
        lines.append(f"- Approval level: unavailable ({exc})")
    try:
        from row_bot.self_health import problems

        found = problems()
        lines.append(f"- Needs attention: {len(found)} (category 'health')" if found else "- Needs attention: nothing")
    except Exception as exc:
        lines.append(f"- Needs attention: unavailable ({exc})")
    try:
        import row_bot.updater as updater

        summary = updater.summary_for_status()
        lines.append(f"- Update: v{summary['available_version']} available" if summary["update_available"]
                     else "- Update: none available")
    except Exception as exc:
        lines.append(f"- Update: unavailable ({exc})")
    lines.append("- For detail, ask one category: model, providers, tools, skills, plugins, mcp, settings, health, "
                 "features, voice, channels, memory, agents, goals, usage, updates.")
    return "\n".join(lines)


def _model_summary_line() -> str:
    try:
        from row_bot.models import _active_model_override, get_current_model
        from row_bot.providers.readiness import evaluate_runtime_readiness

        model = _active_model_override.get("") or get_current_model()
        if not model:
            return "- Model: none chosen yet (the model pill, or Settings › Models › Main model)"
        mode = evaluate_runtime_readiness(model, probe_ollama_tools=False, refresh_provider_status=False).selected_mode
        words = {"agent": "can use tools", "chat_only": "can only chat: tools and actions are off",
                 "blocked": "can't run now"}.get(mode, mode)
        return f"- Model: {model} ({words})"
    except Exception as exc:
        return f"- Model: unavailable ({exc})"


def _query_model(*, compact_pinned: bool = False) -> str:
    try:
        from row_bot.models import (get_current_model, get_context_policy, get_context_size, get_provider_emoji,
                            _active_model_override,
                            get_local_context_mode)
        from row_bot.providers.readiness import evaluate_runtime_readiness
        from row_bot.providers.resolution import resolve_provider_config
        from row_bot.providers.selection import provider_display_label
        default_model = get_current_model()
        override = _active_model_override.get("")
        model = override if override else default_model
        if not model:
            # Nothing is preset (B147): say so, and still list the pinned
            # choices the person can pick from (B158).
            return "\n".join([
                "**Current Model**",
                "- No model is chosen yet. Choose one with the model pill or in Settings › Models.",
                *_pinned_choice_status_lines(
                    "chat",
                    "Pinned Brain Model Choices",
                    value_label="Canonical ref",
                    value_key="canonical_ref",
                    limit=8 if compact_pinned else 0,
                ),
            ])
        try:
            context_policy = get_context_policy(model)
            ctx = context_policy.effective_limit_tokens
        except Exception:
            from types import SimpleNamespace

            ctx = get_context_size(model)
            context_policy = SimpleNamespace(
                effective_limit_tokens=ctx,
                usable_input_tokens=int(ctx * 0.85) if ctx else None,
                capacity_source="compatibility",
                requested_limit_tokens=ctx,
            )
        resolved = resolve_provider_config(model, allow_legacy_local=True)
        local = resolved.execution_location == "local" or resolved.risk_label == "local_private"
        provider_label = resolved.provider_display_name or provider_display_label(resolved.provider_id)
        if resolved.provider_id == "ollama":
            type_label = "Local (Ollama)"
        elif str(resolved.provider_id).startswith("custom_openai_"):
            type_label = "Local custom endpoint" if local else "Custom endpoint"
        else:
            type_label = f"Provider ({provider_label})"
        emoji = get_provider_emoji(model)
        active_runtime = {}
        try:
            from row_bot.agent import get_active_runtime_context

            active_runtime = get_active_runtime_context()
        except Exception:
            active_runtime = {}
        runtime = evaluate_runtime_readiness(model, probe_ollama_tools=False)
        mode_labels = {
            "agent": "Agent Mode",
            "chat_only": "Chat Only - tools and actions are off",
            "blocked": "Unavailable",
        }
        readiness_label = mode_labels.get(runtime.selected_mode, runtime.selected_mode)
        lines = [
            "**Current Model**",
            f"- Model: {emoji} {model}",
            f"- Runtime model: {resolved.runtime_model}",
            f"- Provider: {provider_label}",
            f"- Type: {type_label}",
            f"- Effective context: {ctx:,} tokens" if ctx else "- Effective context: unavailable",
            f"- Usable input: {context_policy.usable_input_tokens:,} tokens"
            if context_policy.usable_input_tokens else "- Usable input: unavailable",
            f"- Capacity source: {context_policy.capacity_source}",
            f"- Readiness: {readiness_label} ({runtime.selection_reason})",
        ]
        selected_runtime = str(active_runtime.get("selected_runtime_mode") or "").strip()
        requested_runtime = str(active_runtime.get("requested_runtime_mode") or "").strip()
        runtime_surface = str(active_runtime.get("runtime_surface") or "").strip()
        if selected_runtime:
            selected_label = mode_labels.get(selected_runtime, selected_runtime)
            requested = f", requested {requested_runtime}" if requested_runtime else ""
            surface = f" on {runtime_surface}" if runtime_surface else ""
            lines.append(f"- Active turn runtime: {selected_label}{requested}{surface}")
        if local:
            lines.append(
                f"- Local context mode: {get_local_context_mode()} "
                f"(requested {context_policy.requested_limit_tokens or 0:,} tokens)"
            )
        else:
            override_text = (
                f"{context_policy.requested_limit_tokens:,} tokens"
                if context_policy.requested_limit_tokens else "Auto"
            )
            lines.append(f"- Provider context override: {override_text}")
        if override and override != default_model:
            lines.append(f"- ⚠️ Override active (global default: {default_model})")
        lines.extend(_pinned_choice_status_lines(
            "chat",
            "Pinned Brain Model Choices",
            value_label="Canonical ref",
            value_key="canonical_ref",
            limit=8 if compact_pinned else 0,
        ))
        return "\n".join(lines)
    except Exception as exc:
        return f"**Current Model**\nError retrieving model info: {exc}"


def _query_channels() -> str:
    try:
        from row_bot.channels.registry import all_channels
        channels = all_channels()
        if not channels:
            return "**Channels**\nNo channels registered."
        lines = ["**Channels**"]
        for ch in channels:
            status = "running" if ch.is_running() else ("configured" if ch.is_configured() else "not configured")
            lines.append(f"- {ch.name}: {status}")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Channels**\nError: {exc}"


def _query_memory() -> str:
    try:
        from row_bot.knowledge_graph import count_entities, count_relations
        entities = count_entities()
        relations = count_relations()
        lines = ["**Knowledge Graph**", f"- Entities: {entities}", f"- Relations: {relations}"]
        try:
            from row_bot.dream_cycle import get_dream_status
            summary = get_dream_status().get("last_summary")
            if summary:
                lines.append(f"- Last Dream Cycle: {summary}")
        except Exception:
            pass
        return "\n".join(lines)
    except Exception as exc:
        return f"**Knowledge Graph**\nError: {exc}"


def _query_skills() -> str:
    try:
        from row_bot.skills import get_default_active_skill_names, get_manual_skill_statuses, is_pinned
        skill_statuses = get_manual_skill_statuses()
        if not skill_statuses:
            return "**Skills**\nNo skills found."
        available_count = sum(1 for _skill, is_available in skill_statuses if is_available)
        off_count = len(skill_statuses) - available_count
        pinned_count = sum(
            1
            for skill, is_available in skill_statuses
            if is_available and is_pinned(skill.name)
        )
        skill_labels = {skill.name: skill.display_name for skill, _is_available in skill_statuses}

        def _default_labels(surface: str) -> str:
            names = get_default_active_skill_names(surface)
            if not names:
                return "None"
            return ", ".join(skill_labels.get(name, name) for name in names)

        lines = [f"**Skill Library** ({available_count} available, {off_count} off, {pinned_count} pinned)"]
        lines.append("- Available means a skill can be selected in chat and suggested when relevant.")
        lines.append("- Pinned skills start active in new chats, tasks, designer threads, and developer threads; users can remove them per workflow.")
        lines.append("- Designer threads also start with Design Creator when it is available.")
        lines.append("- Tool guides are separate tool instructions and are not listed here.")
        lines.append(f"- Default chat skills: {_default_labels('chat')}")
        lines.append(f"- Default task skills: {_default_labels('task')}")
        lines.append(f"- Default designer skills: {_default_labels('designer')}")
        lines.append(f"- Default developer skills: {_default_labels('developer')}")
        for skill, is_available in skill_statuses:
            status = "Available" if is_available else "Off"
            markers: list[str] = []
            if is_available and is_pinned(skill.name):
                markers.append("Pinned default")
            if skill.name in get_default_active_skill_names("designer") and not is_pinned(skill.name):
                markers.append("Designer default")
            marker_text = f" ({', '.join(markers)})" if markers else ""
            lines.append(f"- {skill.display_name}: {status}{marker_text}")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Skills**\nError: {exc}"


def _query_tools() -> str:
    try:
        from row_bot.tools.registry import get_all_tools, is_enabled
        tools = get_all_tools()
        if not tools:
            return "**Tools**\nNo tools registered."
        active_developer = False
        try:
            from row_bot.developer.tool_context import get_thread_id, get_workspace_id, infer_workspace_id_from_thread
            active_developer = bool(get_workspace_id() or infer_workspace_id_from_thread(get_thread_id()))
        except Exception:
            active_developer = False

        enabled = [t for t in tools if is_enabled(t.name)]
        contextual = [t for t in tools if t.name == "developer" and active_developer and not is_enabled(t.name)]
        disabled = [
            t for t in tools
            if not is_enabled(t.name) and not (t.name == "developer" and active_developer)
        ]
        suffix = f", {len(contextual)} contextual" if contextual else ""
        lines = [
            "**Tools**",
            f"- Global catalog: {len(enabled)} enabled{suffix}, {len(disabled)} disabled",
        ]
        profile_scope = _active_thread_profile_scope()
        profile = profile_scope.get("profile")
        runtime_allowlist = _clean_tool_ids(profile_scope.get("runtime_allowlist") or [])
        runtime_surface = str(profile_scope.get("runtime_surface") or "").strip()
        if isinstance(profile, dict) or runtime_allowlist:
            lines.extend(["", "**Effective Thread Tool Scope**"])
            if isinstance(profile, dict):
                active_label = profile.get("display_name") or profile.get("slug") or "active profile"
                state = "enabled" if profile.get("enabled", True) else "disabled"
                lines.extend([
                    f"- Active profile: {active_label} ({profile.get('slug')}, {state})",
                    f"- {_PROFILE_TOOLS} Its capability: {_capability(profile)}.",
                ])
            if runtime_allowlist:
                lines.append(f"- Runtime allow-list for this turn: {_format_selected_tool_ids(runtime_allowlist)}")
            if runtime_surface.startswith("agent_child"):
                lines.append(
                    "- skill_search and skill_load, when present, are separate from the tools; "
                    "loaded skills can guide the child but cannot add tools or relax its "
                    "profile, approval, or workspace boundaries."
                )
            lines.extend(["", "Global tools:"])
        for t in enabled:
            lines.append(f"- ✅ {t.display_name}")
        for t in contextual:
            lines.append(f"- contextual: {t.display_name} (active for the current Developer workspace)")
        for t in disabled:
            lines.append(f"- ❌ {t.display_name}")
        try:
            from row_bot.plugins import registry as plugin_registry

            plugin_records = plugin_registry.get_enabled_plugin_tool_records()
            if plugin_records:
                lines.append("")
                lines.append("Plugin tools:")
                for item in plugin_records:
                    runtime_name = str(item.get("runtime_name") or "").strip()
                    if not runtime_name:
                        continue
                    plugin_name = str(item.get("plugin_name") or item.get("plugin_id") or "Plugin")
                    label = str(item.get("label") or runtime_name)
                    lines.append(f"- ✅ {plugin_name}: {label} ({runtime_name})")
            else:
                lines.append("")
                lines.append("Plugin tools: none currently registered.")
        except Exception as exc:
            lines.append("")
            lines.append(f"Plugin tools: unavailable ({exc})")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Tools**\nError: {exc}"


def _query_plugins() -> str:
    try:
        from row_bot.channels import registry as channel_registry
        from row_bot.plugins import loader as plugin_loader
        from row_bot.plugins import registry as plugin_registry
        from row_bot.plugins import state as plugin_state

        summary = plugin_loader.get_load_summary()
        manifests = sorted(plugin_registry.get_loaded_manifests(), key=lambda item: item.name.lower())
        enabled_manifests = [manifest for manifest in manifests if plugin_state.is_plugin_enabled(manifest.id)]
        plugin_records = plugin_registry.get_enabled_plugin_tool_records()
        stale_results = [result for result in summary.get("results", []) if getattr(result, "stale", False)]
        failed_results = [result for result in summary.get("results", []) if not getattr(result, "success", False)]

        lines = [
            "**Plugins**",
            (
                f"- Installed/loaded: {len(manifests)}; enabled: {len(enabled_manifests)}; "
                f"failed: {len(failed_results)}; stale legacy: {len(stale_results)}"
            ),
        ]
        if manifests:
            lines.append("- Plugin states:")
            for manifest in manifests:
                state_label = "enabled" if plugin_state.is_plugin_enabled(manifest.id) else "disabled"
                lines.append(f"  - {manifest.name} ({manifest.id}) v{manifest.version}: {state_label}")
        if plugin_records:
            lines.append("- Enabled plugin tools:")
            for item in plugin_records:
                runtime_name = str(item.get("runtime_name") or "").strip()
                if not runtime_name:
                    continue
                plugin_name = str(item.get("plugin_name") or item.get("plugin_id") or "Plugin")
                label = str(item.get("label") or runtime_name)
                lines.append(f"  - {plugin_name}: {label} ({runtime_name})")
        else:
            lines.append("- Enabled plugin tools: none")

        plugin_channels: list[str] = []
        for channel in channel_registry.all_channels():
            source = channel_registry.get_source(channel.name)
            if source.kind == "plugin":
                plugin_channels.append(f"{source.label or source.plugin_id}: {channel.display_name} ({channel.name})")
        if plugin_channels:
            lines.append("- Plugin channels:")
            lines.extend(f"  - {item}" for item in sorted(plugin_channels))

        if stale_results:
            lines.append("- Stale legacy plugins moved aside:")
            for result in stale_results:
                detail = result.stale_path or "stale_plugins"
                lines.append(f"  - {result.plugin_id}: {detail}")
        if failed_results:
            lines.append("- Failed plugins:")
            for result in failed_results:
                lines.append(f"  - {result.plugin_id}: {result.error or 'unknown error'}")
        if not manifests and not stale_results and not failed_results:
            lines.append("- No plugins installed.")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Plugins**\nError: {exc}"


def _query_mcp() -> str:
    try:
        from row_bot.mcp_client.runtime import get_status_summary
        summary = get_status_summary()
        lines = [
            "**MCP Client**",
            f"- Global enable: {'on' if summary.get('enabled') else 'off'}",
            f"- MCP SDK: {'available' if summary.get('sdk_available') else 'missing'}",
            f"- Servers: {summary.get('enabled_server_count', 0)} enabled / {summary.get('server_count', 0)} configured / {summary.get('connected_server_count', 0)} connected",
            f"- Tools: {summary.get('enabled_tool_count', 0)} enabled / {summary.get('tool_count', 0)} discovered",
            f"- Approval-gated tools: {summary.get('destructive_tool_count', 0)}",
        ]
        servers = summary.get("servers", {})
        if servers:
            from row_bot.agent import current_app_scope, get_current_thread_id
            from row_bot.integrations.scope import servers_off
            left_out = set((current_app_scope() or {}).get("exclude_servers") or [])
            off = servers_off(get_current_thread_id()) & left_out
            lines.append("- Server details:")
            for name, status in sorted(servers.items()):
                detail = f"  - {name}: {status.get('status', 'unknown')} ({status.get('transport', 'stdio')})"
                if status.get("last_error"):
                    detail += f" — last error: {status['last_error']}"
                # Its tools aren't in this turn: say why, not that it's broken.
                if name in off:
                    detail += " — switched off in this chat; an @mention won't use it until it's switched on in + › Apps"
                elif name in left_out:
                    detail += " — not in this turn: the message @mentions other apps"
                lines.append(detail)
        return "\n".join(lines)
    except Exception as exc:
        return f"**MCP Client**\nError: {exc}"


def _query_providers() -> str:
    try:
        from row_bot.providers.status import summarize_providers
        return summarize_providers()
    except Exception as exc:
        return f"**Providers**\nError: {exc}"


def _query_insights() -> str:
    try:
        from row_bot.insights import get_active_insights, get_insights_meta

        active = get_active_insights()
        meta = get_insights_meta()
        lines = ["**Insights**"]
        lines.append(f"- Active: {len(active)}")
        lines.append(f"- Last analysis: {meta.get('last_analysis') or 'never'}")
        total = meta.get("total_generated")
        if total is not None:
            lines.append(f"- Total generated: {total}")
        if active:
            lines.append("- Active insights:")
            for item in active[:8]:
                category = item.get("category", "unknown")
                severity = item.get("severity", "info")
                status = item.get("status", "new")
                title = item.get("title", "Untitled insight")
                lines.append(f"  - [{severity}/{category}/{status}] {title}")
            if len(active) > 8:
                lines.append(f"  - … and {len(active) - 8} more")
        else:
            lines.append("- No active insights")
        try:
            from row_bot.evolution import TERMINAL_PROPOSAL_STATUSES, learning_draft, list_action_runs, list_proposals

            proposals = list_proposals()
            # Learning's drafts wait to be tested in Learning (L3b): their text doesn't reach the agent before.
            active_proposals = [
                proposal
                for proposal in proposals
                if proposal.get("status") not in TERMINAL_PROPOSAL_STATUSES
                and not learning_draft(proposal)
            ]
            lines.append(f"- Active proposals: {len(active_proposals)}")
            for proposal in active_proposals[:6]:
                lines.append(
                    "  - "
                    f"[{proposal.get('proposal_type')}/{proposal.get('risk')}/{proposal.get('status')}] "
                    f"{proposal.get('title')} ({proposal.get('id')})"
                )
            runs = list_action_runs(limit=3)
            if runs:
                lines.append("- Recent action runs:")
                for run in runs:
                    lines.append(
                        f"  - [{run.get('action_type')}/{run.get('result')}] {run.get('proposal_id')}"
                    )
        except Exception as exc:
            lines.append(f"- Proposal data unavailable: {exc}")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Insights**\nError: {exc}"


def _query_evolution() -> str:
    try:
        from row_bot.evolution import evolution_summary

        return evolution_summary()
    except Exception as exc:
        return f"**Controlled Self-Evolution**\nError: {exc}"


def _query_identity() -> str:
    try:
        from row_bot.identity import get_identity_config, is_self_improvement_enabled
        cfg = get_identity_config()
        name = cfg.get("name", "Row-Bot")
        personality = cfg.get("personality", "")
        self_improve = is_self_improvement_enabled()
        lines = [
            "**Identity**",
            f"- Name: {name}",
            f"- Personality: {personality or '(not set)'}",
            f"- Self-improvement: {'enabled' if self_improve else 'disabled'}",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"**Identity**\nError: {exc}"


def _query_tasks() -> str:
    try:
        from row_bot.tasks import diagnose_task_schema, list_tasks
        diag = diagnose_task_schema()
        tasks = list_tasks()
        lines: list[str] = []
        if not diag.get("ok"):
            missing = ", ".join(diag.get("missing_tables") or []) or "columns"
            lines += ["**Workflow database**", f"- Needs repair: missing {missing}"
                      + (f"; {diag.get('error')}" if diag.get("error") else ""), ""]
        if not tasks:
            return "\n".join(lines + ["**Scheduled Tasks**", "No tasks configured."])
        lines.append(f"**Scheduled Tasks** ({len(tasks)} total)")
        for task in tasks[:10]:  # Show at most 10
            status = "enabled" if task.get("enabled", True) else "disabled"
            lines.append(f"- {task.get('name', 'Unnamed')}: {task.get('schedule', 'no schedule')} ({status})")
        if len(tasks) > 10:
            lines.append(f"  ... and {len(tasks) - 10} more")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Scheduled Tasks**\nError: {exc}"


def _short_status_text(value: object, limit: int = 96) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _counts_by(items: list[dict], key: str) -> str:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unset").strip() or "unset"
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return "none"
    return ", ".join(
        f"{name} {count}"
        for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    )


def _clean_tool_ids(items: object) -> list[str]:
    if isinstance(items, str):
        raw_items = [items]
    elif isinstance(items, dict):
        raw_items = []
    else:
        try:
            raw_items = list(items or [])
        except TypeError:
            raw_items = [items]
    return [
        str(item).strip()
        for item in raw_items
        if str(item or "").strip()
    ]


# A profile lists no tools (F31): what a turn may do is its capability and the approval level.
_PROFILE_TOOLS = ("Profiles list no tools: every enabled tool is available, and the profile's capability "
                  "and the approval level decide what may run.")


def _capability(profile: dict) -> str:
    tool_policy = profile.get("tool_policy_json") if isinstance(profile.get("tool_policy_json"), dict) else {}
    return str(tool_policy.get("capability") or "read_only")


def _active_runtime_context() -> dict:
    try:
        from row_bot.agent import get_active_runtime_context

        context = get_active_runtime_context()
    except Exception:
        return {}
    return dict(context) if isinstance(context, dict) else {}


def _active_runtime_thread_id() -> str:
    context = _active_runtime_context()
    return str((context or {}).get("thread_id") or "").strip()


def _tool_catalog_lookup() -> dict[str, dict]:
    try:
        from row_bot.agent_tool_catalog import list_agent_tool_catalog

        catalog = list_agent_tool_catalog(include_unavailable=True)
    except Exception:
        catalog = []
    lookup: dict[str, dict] = {}
    for record in catalog:
        if not isinstance(record, dict):
            continue
        for key in ("id", "runtime_name"):
            value = str(record.get(key) or "").strip()
            if value:
                lookup.setdefault(value, record)
    return lookup


def _tool_label_for_id(tool_id: str, catalog_lookup: dict[str, dict] | None = None) -> str:
    clean_id = str(tool_id or "").strip()
    if not clean_id:
        return ""
    lookup = catalog_lookup if isinstance(catalog_lookup, dict) else _tool_catalog_lookup()
    record = lookup.get(clean_id)
    if isinstance(record, dict):
        label = str(record.get("label") or record.get("runtime_name") or clean_id).strip()
        label = re.sub(r"^[^A-Za-z0-9]+", "", label).strip()
        if label:
            return label
    try:
        tool = registry.get_tool(clean_id)
    except Exception:
        tool = None
    if tool is not None:
        return _tool_display_label(tool)
    return clean_id


def _format_selected_tool_ids(tool_ids: object, *, limit: int = 12) -> str:
    clean_ids = _clean_tool_ids(tool_ids)
    if not clean_ids:
        return "none"
    lookup = _tool_catalog_lookup()
    labels: list[str] = []
    for tool_id in clean_ids[:limit]:
        label = _tool_label_for_id(tool_id, lookup)
        labels.append(f"{label or tool_id} ({tool_id})")
    if len(clean_ids) > limit:
        labels.append(f"... and {len(clean_ids) - limit} more")
    return ", ".join(labels)


def _active_thread_profile_scope() -> dict:
    context = _active_runtime_context()
    thread_id = str(context.get("thread_id") or "").strip()
    runtime_surface = str(context.get("runtime_surface") or "").strip()
    runtime_profile_ref = str(context.get("agent_profile_id") or "").strip()
    runtime_allowlist = _clean_tool_ids(context.get("tool_allowlist") or [])
    thread_profile_ref = ""
    if thread_id:
        try:
            from row_bot.threads import _get_thread_agent_profile

            pointer = _get_thread_agent_profile(thread_id)
        except Exception:
            pointer = {"id": "", "slug": ""}
        if isinstance(pointer, dict):
            thread_profile_ref = str(pointer.get("id") or pointer.get("slug") or "").strip()

    profile_ref = runtime_profile_ref or thread_profile_ref
    profile = None
    if profile_ref:
        try:
            from row_bot.agent_profiles import get_agent_profile

            profile = get_agent_profile(profile_ref, enabled_only=False)
        except Exception:
            profile = None

    return {
        "thread_id": thread_id,
        "runtime_surface": runtime_surface,
        "profile_ref": profile_ref,
        "profile": profile,
        "runtime_allowlist": runtime_allowlist,
    }


def _query_agents() -> str:
    try:
        from row_bot.agent_runs import (
            TERMINAL_STATUSES,
            list_agent_runs,
            list_agent_write_locks,
        )

        sampled_runs = list_agent_runs(limit=200)
        from row_bot.agent_runner import child_dispatch_state
        from row_bot.agent_settings import load_agent_runtime_settings

        runtime_settings = load_agent_runtime_settings()
        dispatch = child_dispatch_state()
        current_runs = [
            run for run in sampled_runs
            if str(run.get("status") or "") not in TERMINAL_STATUSES
        ]
        writer_locks = list_agent_write_locks()
        lines = [
            "**Agents**",
            f"- Current runs: {len(current_runs)} active/attention from {len(sampled_runs)} sampled",
            f"- By kind: {_counts_by(sampled_runs, 'kind')}",
            f"- By status: {_counts_by(sampled_runs, 'status')}",
            (
                "- Defaults: "
                f"depth {runtime_settings.max_spawn_depth}; "
                f"{runtime_settings.max_concurrent_children} active per parent; "
                f"{runtime_settings.max_active_children_global} active globally; "
                f"child timeout {runtime_settings.child_timeout_seconds or 'off'}"
            ),
            f"- Dispatcher: {dispatch['active']} active, {dispatch['queued']} queued",
            f"- Writer locks: {len(writer_locks)} active",
        ]
        if not sampled_runs:
            lines.append("No durable Agent Runs recorded.")
            return "\n".join(lines)

        if not current_runs:
            lines.append("No current Agent Runs.")
        else:
            lines.append("Current runs:")
        for run in current_runs[:8]:
            label = _short_status_text(
                run.get("display_name") or run.get("prompt") or run.get("id"),
                72,
            )
            status = str(run.get("status") or "queued")
            kind = str(run.get("kind") or "subagent")
            profile = str(run.get("profile_display_name") or run.get("profile_slug") or "").strip()
            profile_label = f", profile {profile}" if profile else ""
            progress = ""
            if int(run.get("model_iterations_max") or 0):
                progress = (
                    f", rounds {run.get('model_iterations_used', 0)}/"
                    f"{run.get('model_iterations_max', 0)}, depth {run.get('depth', 0)}"
                )
            detail = _short_status_text(
                run.get("status_message") or run.get("summary") or run.get("error"),
                90,
            )
            suffix = f" - {detail}" if detail else ""
            lines.append(f"- {label} [{kind}/{status}{profile_label}{progress}]{suffix}")
        if len(current_runs) > 8:
            lines.append(f"- ... and {len(current_runs) - 8} more current runs")

        if writer_locks:
            lines.append("Writer locks:")
            for lock in writer_locks[:5]:
                key = _short_status_text(lock.get("lock_key"), 64)
                run_id = str(lock.get("run_id") or "")
                workspace = _short_status_text(lock.get("workspace_path") or lock.get("workspace_id"), 72)
                workspace_label = f" ({workspace})" if workspace else ""
                lines.append(f"- {key}: run {run_id}{workspace_label}")
            if len(writer_locks) > 5:
                lines.append(f"- ... and {len(writer_locks) - 5} more locks")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Agents**\nError: {exc}"


def _query_agent_profiles() -> str:
    try:
        from row_bot.agent_profiles import list_agent_profiles

        profiles = list_agent_profiles(enabled_only=False)
        enabled = [profile for profile in profiles if profile.get("enabled", True)]
        lines = [
            "**Agent Profiles**",
            f"- Profiles: {len(enabled)} enabled / {len(profiles)} total",
            f"- Sources: {_counts_by(profiles, 'source')}",
            f"- Scopes: {_counts_by(profiles, 'scope')}",
            f"- Tools: {_PROFILE_TOOLS}",
        ]
        profile_scope = _active_thread_profile_scope()
        thread_id = str(profile_scope.get("thread_id") or "").strip()
        active = profile_scope.get("profile")
        active_ref = str(profile_scope.get("profile_ref") or "").strip()
        if isinstance(active, dict):
            state = "enabled" if active.get("enabled", True) else "disabled"
            lines.append(
                "- Active thread profile: "
                f"{active.get('display_name') or active.get('slug')} "
                f"({active.get('slug')}, {state}, {_capability(active)})"
            )
        elif active_ref:
            lines.append(f"- Active thread profile: {active_ref} (not found)")
        elif thread_id:
            lines.append("- Active thread profile: implicit default")
        else:
            lines.append("- Active thread profile: no active runtime thread")

        if not profiles:
            lines.append("No Agent Profiles are available.")
            return "\n".join(lines)

        lines.append("Available profiles:")
        for profile in profiles[:10]:
            state = "enabled" if profile.get("enabled", True) else "disabled"
            skill_policy = profile.get("skill_policy_json") if isinstance(profile.get("skill_policy_json"), dict) else {}
            policy_bits = [_capability(profile), f"skills={len(skill_policy.get('skills_override') or [])}"]
            description = _short_status_text(
                profile.get("when_to_use") or profile.get("description"),
                90,
            )
            suffix = f" - {description}" if description else ""
            lines.append(
                f"- {profile.get('display_name') or profile.get('slug')} "
                f"[{profile.get('slug')}, {profile.get('source')}, {state}, "
                f"{', '.join(policy_bits)}]{suffix}"
            )
        if len(profiles) > 10:
            lines.append(f"- ... and {len(profiles) - 10} more profiles")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Agent Profiles**\nError: {exc}"


def _query_goals() -> str:
    try:
        from row_bot.goals import (
            GOAL_TERMINAL_STATUSES,
            default_goal_max_turns,
            get_current_goal,
            list_goals,
            turn_words,
        )

        sampled_goals = list_goals(limit=200)
        current_goals = [
            goal for goal in sampled_goals
            if str(goal.get("status") or "") not in GOAL_TERMINAL_STATUSES
        ]
        lines = [
            "**Goals**",
            f"- Current goals: {len(current_goals)} active/attention from {len(sampled_goals)} sampled",
            f"- By status: {_counts_by(sampled_goals, 'status')}",
            f"- Default turn limit: {default_goal_max_turns() or 'none'}",
        ]
        thread_id = _active_runtime_thread_id()
        if thread_id:
            current = get_current_goal(thread_id, include_terminal=True)
            if current:
                objective = _short_status_text(current.get("objective"), 90)
                lines.append(
                    "- Current thread: "
                    f"{current.get('status')} - {objective} "
                    f"(turns {turn_words(current)})"
                )
            else:
                lines.append("- Current thread: no visible goal")
        else:
            lines.append("- Current thread: no active runtime thread")

        if not sampled_goals:
            lines.append("No Goal Mode records yet.")
            return "\n".join(lines)

        if not current_goals:
            lines.append("No current goals.")
        else:
            lines.append("Current goals:")
        for goal in current_goals[:8]:
            objective = _short_status_text(goal.get("objective"), 76)
            status = str(goal.get("status") or "active")
            progress = _short_status_text(
                goal.get("last_progress") or goal.get("last_blocker") or goal.get("status_reason"),
                90,
            )
            verifier = ""
            if int(goal.get("verifier_failures") or 0):
                verifier = f", verifier failures {goal.get('verifier_failures')}"
            suffix = f" - {progress}" if progress else ""
            lines.append(
                f"- {objective} [{status}, turns {turn_words(goal)}{verifier}]{suffix}"
            )
        if len(current_goals) > 8:
            lines.append(f"- ... and {len(current_goals) - 8} more current goals")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Goals**\nError: {exc}"


def _query_logs() -> str:
    """Recent warnings and errors from the log file."""
    try:
        from row_bot.logging_config import read_recent_logs
        entries = read_recent_logs(n=50)
        # Filter to WARNING+ and exclude row_bot_status calls to avoid recursion
        filtered = [
            e for e in entries
            if e.get("level", "") in ("WARNING", "ERROR", "CRITICAL")
            and "row_bot_status" not in e.get("msg", "")
        ][:15]
        if not filtered:
            return "**Recent Logs**\nNo warnings or errors in the recent log."
        lines = [f"**Recent Logs** (WARNING+ level, newest first)"]
        for e in filtered:
            ts = e.get("ts", "?")[-8:]  # HH:MM:SS
            lvl = e.get("level", "?")[:4]
            msg = e.get("msg", "")[:200]
            lines.append(f"- [{ts}] {lvl}: {msg}")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Recent Logs**\nError reading logs: {exc}"


def _query_errors() -> str:
    """Recent errors with tracebacks for diagnosis."""
    try:
        from row_bot.logging_config import read_recent_logs
        entries = read_recent_logs(n=100)
        errors = [
            e for e in entries
            if e.get("level", "") in ("ERROR", "CRITICAL")
            and "row_bot_status" not in e.get("msg", "")
        ][:10]
        if not errors:
            return "**Recent Errors**\nNo errors in the recent log."
        lines = [f"**Recent Errors** (newest first)"]
        for e in errors:
            ts = e.get("ts", "?")
            msg = e.get("msg", "")[:300]
            lines.append(f"- [{ts}] {msg}")
            exc = e.get("exc", "")
            if exc:
                # Show last 3 lines of traceback to keep tokens low
                tb_lines = exc.strip().splitlines()[-3:]
                for tb in tb_lines:
                    lines.append(f"  {tb.strip()[:150]}")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Recent Errors**\nError reading logs: {exc}"


def _query_vision() -> str:
    """Vision / camera model and settings."""
    try:
        from row_bot.vision import _load_settings
        settings = _load_settings()
        # An empty Vision model means "Same as chat model" (decision 11).
        chosen = settings.get("model") or ""
        if chosen:
            model = chosen
        else:
            from row_bot.models import get_current_model

            model = get_current_model()
        enabled = settings.get("enabled", True)
        camera = settings.get("camera_index", 0)
        provider_id = ""
        runtime_model = str(model or "")
        provider_label = "Ollama"
        probe: dict = {}
        try:
            from row_bot.providers.selection import parse_model_ref, provider_display_label

            parsed = parse_model_ref(str(model or ""))
            if parsed:
                provider_id, runtime_model = parsed
            else:
                try:
                    from row_bot.models import get_cloud_provider

                    provider_id = str(get_cloud_provider(str(model or "")) or "ollama")
                except Exception:
                    provider_id = "ollama"
            if provider_id == "local":
                provider_id = "ollama"
            if provider_id.startswith("custom_openai_"):
                try:
                    from row_bot.providers.custom import get_custom_endpoint

                    endpoint = get_custom_endpoint(provider_id) or {}
                    provider_label = str(endpoint.get("display_name") or endpoint.get("name") or provider_display_label(provider_id))
                    if isinstance(endpoint.get("last_probe"), dict):
                        probe = dict(endpoint["last_probe"])
                except Exception:
                    provider_label = provider_display_label(provider_id)
            else:
                provider_label = provider_display_label(provider_id)
        except Exception:
            provider_id = "ollama"
            provider_label = "Ollama"
        vision_ok = probe.get("vision_ok") if probe else None
        vision_error = str(probe.get("vision_error") or "") if probe else ""
        vision_skip = str(probe.get("vision_probe_skip_reason") or "") if probe else ""
        compatibility: dict = {}
        try:
            from row_bot.vision import vision_model_compatibility

            compatibility = vision_model_compatibility(str(model or ""))
        except Exception:
            compatibility = {}
        incompat_reason = ""
        if compatibility.get("explicit") and not compatibility.get("usable"):
            incompat_reason = str(compatibility.get("reason") or "capability metadata says this model is not compatible with vision")
        if vision_ok is False and _vision_probe_error_is_inconclusive(vision_error):
            vision_ok = None
        if incompat_reason:
            readiness = "vision disabled for endpoint" if "manual" in incompat_reason.lower() else "vision incompatible"
        elif vision_ok is True:
            readiness = "vision verified"
        elif vision_ok is False:
            readiness = "vision failed"
        elif provider_id.startswith("custom_openai_"):
            readiness = "vision unverified"
        else:
            readiness = "vision inferred"
        lines = [
            "**Vision**",
            f"- Model: {model or 'not chosen'}{'' if chosen else ' (same as chat model)'}",
            f"- Runtime model: {runtime_model}",
            f"- Provider: {provider_label}",
            f"- Enabled: {'yes' if enabled else 'no'}",
            f"- Camera index: {camera}",
            f"- Vision readiness: {readiness}",
        ]
        if probe:
            if probe.get("vision_model"):
                lines.append(f"- Vision probe model: {probe.get('vision_model')}")
            if probe.get("vision_content_format"):
                lines.append(f"- Vision content format: {probe.get('vision_content_format')}")
            if vision_skip:
                lines.append(f"- Vision probe skipped: {vision_skip}")
            if vision_error and (vision_ok is False or _vision_probe_error_is_inconclusive(vision_error)):
                label = "Vision probe error" if vision_ok is False else "Vision probe note"
                lines.append(f"- {label}: {vision_error}")
        if incompat_reason:
            lines.append(f"- Vision compatibility: {incompat_reason}")
        lines.extend(_pinned_choice_status_lines(
            "vision",
            "Pinned Vision Model Choices",
            value_label="Selection value",
            value_key="canonical_ref",
        ))
        return "\n".join(lines)
    except Exception as exc:
        return f"**Vision**\nError: {exc}"


def _vision_probe_error_is_inconclusive(error: str) -> bool:
    text = str(error or "").strip().lower()
    return text.startswith("probe inconclusive") or text in {
        "unexpected response: <empty>",
        "unexpected response: ",
    }


def _query_image_gen() -> str:
    """Image generation model."""
    try:
        from row_bot.tools.image_gen_tool import _get_configured_selection
        from row_bot.tools.registry import is_enabled
        selection = _get_configured_selection()
        lines = [
            "**Image Generation**",
            f"- Tool: {'enabled' if is_enabled('image_gen') else 'disabled'}",
            f"- Model: {selection or 'not chosen (choose one in Settings › Models)'}",
        ]
        lines.extend(_pinned_choice_status_lines(
            "image",
            "Pinned Image Model Choices",
            value_label="Config value",
            value_key="config_value",
        ))
        return "\n".join(lines)
    except Exception as exc:
        return f"**Image Generation**\nError: {exc}"


def _query_video_gen() -> str:
    """Video generation model."""
    try:
        from row_bot.tools.video_gen_tool import _get_configured_selection
        from row_bot.tools.registry import is_enabled
        selection = _get_configured_selection()
        lines = [
            "**Video Generation**",
            f"- Tool: {'enabled' if is_enabled('video_gen') else 'disabled'}",
            f"- Model: {selection or 'not chosen (choose one in Settings › Models)'}",
        ]
        lines.extend(_pinned_choice_status_lines(
            "video",
            "Pinned Video Model Choices",
            value_label="Config value",
            value_key="config_value",
        ))
        return "\n".join(lines)
    except Exception as exc:
        return f"**Video Generation**\nError: {exc}"


def _query_voice() -> str:
    """Voice runtime, speech settings, and realtime diagnostics."""
    try:
        lines = ["**Voice & Speech**"]
        lines.append("- User-facing modes: Talk, Dictate")
        lines.append("- Dictate policy: STT-only; never sends to the LLM until the user presses Send")
        lines.append("- Realtime role: voice transport/backchannel for normal Row-Bot work")
        # TTS
        try:
            tts_path = _DATA_DIR / "tts_settings.json"
            if tts_path.is_file():
                tts = json.loads(tts_path.read_text(encoding="utf-8"))
                lines.append(f"- TTS enabled: {'yes' if tts.get('enabled', False) else 'no'}")
                lines.append(f"- TTS voice: {tts.get('voice', 'af_heart')}")
                lines.append(f"- TTS speed: {tts.get('speed', 1.0)}")
            else:
                lines.append("- TTS: not configured")
        except Exception:
            lines.append("- TTS: error reading settings")
        # STT (Whisper)
        try:
            voice_path = _DATA_DIR / "voice_settings.json"
            if voice_path.is_file():
                voice = json.loads(voice_path.read_text(encoding="utf-8"))
                lines.append(f"- Whisper model: {voice.get('whisper_model', 'small')}")
            else:
                lines.append("- Whisper model: small (default)")
        except Exception:
            lines.append("- Whisper: error reading settings")
        try:
            from row_bot.voice.runtime import load_voice_runtime_settings

            runtime = load_voice_runtime_settings()
            lines.append(f"- Talk provider: {runtime.talk_provider}")
            lines.append(f"- Talk model: {runtime.talk_model}")
            lines.append(f"- Dictation provider: {runtime.dictation_provider}")
            lines.append(f"- Dictation model: {runtime.dictation_model}")
            lines.append(f"- Speech output provider: {runtime.speech_output_provider}")
            lines.append(f"- Speech output model: {runtime.speech_output_model}")
            lines.append(f"- Speech output voice: {runtime.speech_output_voice}")
            lines.append(f"- Captions: {'on' if runtime.captions_enabled else 'off'}")
            lines.append(f"- Realtime fallback: {'on' if runtime.realtime_fallback_to_local else 'off'}")
        except Exception:
            lines.append("- Voice runtime: error reading settings")
        try:
            from row_bot.voice.openai_realtime import OpenAIRealtimeProvider
            from row_bot.voice.agent_bridge import (
                REALTIME_ALLOWED_BRIDGE_TOOLS,
                REALTIME_DIRECT_TOOL_POLICY,
                REALTIME_WAIT_TOOL,
                VOICE_BRAIN_STRATEGY,
            )

            realtime = OpenAIRealtimeProvider().status()
            lines.append(f"- OpenAI Realtime: {'ready' if realtime.ready else 'not configured'}")
            lines.append(f"- Realtime brain strategy: {VOICE_BRAIN_STRATEGY}")
            lines.append(f"- Realtime direct normal-tool access: {REALTIME_DIRECT_TOOL_POLICY}")
            lines.append(f"- Realtime bridge tools: {', '.join(REALTIME_ALLOWED_BRIDGE_TOOLS)}")
            lines.append(f"- Realtime quiet idle tool: {REALTIME_WAIT_TOOL}")
            lines.append("- Realtime credential safety: browser receives ephemeral client secrets only; long-lived provider keys stay server-side")
        except Exception:
            lines.append("- OpenAI Realtime: unavailable")
        try:
            from row_bot.runtime import executions

            active = executions.generation_registry.active()
            if not active:
                lines.append("- Active Row-Bot run: none")
            else:
                lines.append(f"- Active Row-Bot runs: {len(active)}")
                for handle in active[:3]:
                    lines.append(
                        f"  - {handle.conversation_id}: {handle.status}; "
                        f"approval={'yes' if handle.approval_id else 'no'}; "
                        f"cancel={'requested' if handle.cancel_scope.is_cancelled() else 'available'}"
                    )
        except Exception:
            lines.append("- Active Row-Bot run: unavailable")
        try:
            from row_bot.logging_config import read_recent_logs

            realtime_logs = [
                entry
                for entry in read_recent_logs(80)
                if "voice.realtime.pipeline" in str(entry.get("msg") or "")
                   or "OpenAI Realtime" in str(entry.get("msg") or "")
            ][:5]
            if realtime_logs:
                lines.append("- Recent realtime diagnostics:")
                for entry in realtime_logs:
                    msg = str(entry.get("msg") or "").replace("\n", " ")
                    msg = re.sub(r"(sk|ek)_[A-Za-z0-9_\-]+", r"\1_***", msg)
                    lines.append(f"  - {entry.get('ts', '')} {entry.get('level', '')}: {msg[:300]}")
            else:
                lines.append("- Recent realtime diagnostics: none")
        except Exception:
            lines.append("- Recent realtime diagnostics: unavailable")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Voice & Speech**\nError: {exc}"


def _query_config() -> str:
    """Miscellaneous configuration: context caps, dream cycle, wiki vault, memory extraction."""
    lines = ["**Configuration**"]
    try:
        # Context size caps
        from row_bot.models import get_cloud_context_override, get_local_context_mode, get_user_context_size
        lines.append(
            f"- Local context: {get_local_context_mode()} ({get_user_context_size():,} requested tokens)"
        )
        cloud_override = get_cloud_context_override()
        lines.append(
            f"- Provider context override: {cloud_override:,} tokens"
            if cloud_override else "- Provider context override: Auto"
        )
    except Exception:
        pass
    try:
        # Dream cycle
        dc_path = _DATA_DIR / "dream_config.json"
        if dc_path.is_file():
            dc = json.loads(dc_path.read_text(encoding="utf-8"))
            lines.append(f"- Dream cycle: {'enabled' if dc.get('enabled', True) else 'disabled'}"
                         f" (window {dc.get('window_start', 1)}:00–{dc.get('window_end', 5)}:00)")
        else:
            lines.append("- Dream cycle: enabled (default 1:00–5:00)")
    except Exception:
        pass
    try:
        # Memory extraction
        me_path = _DATA_DIR / "memory_extraction_state.json"
        if me_path.is_file():
            me = json.loads(me_path.read_text(encoding="utf-8"))
            last = me.get("last_extraction", "never")
            entities = me.get("entities_saved", 0)
            lines.append(f"- Memory extraction: last run {last}, {entities} entities saved")
        else:
            lines.append("- Memory extraction: no runs yet")
    except Exception:
        pass
    try:
        # Wiki vault
        wv_path = _DATA_DIR / "wiki_config.json"
        if wv_path.is_file():
            wv = json.loads(wv_path.read_text(encoding="utf-8"))
            lines.append(f"- Wiki vault: {'enabled' if wv.get('enabled', False) else 'disabled'}")
        else:
            lines.append("- Wiki vault: disabled (default)")
    except Exception:
        pass
    return "\n".join(lines)


def _query_version() -> str:
    from row_bot.version import __version__
    return f"**Row-Bot Version**: v{__version__}"


def _query_features() -> str:
    """What Row-Bot does, by area, from its own guides for this version (never a hand-kept list)."""
    from row_bot import guides

    try:
        pages = guides.pages()
    except guides.GuidesUnavailable as exc:
        return f"**Row-Bot features**\n{exc}"
    groups: dict[str, list] = {}
    for page in pages:
        if page.group not in {"", "reference"}:  # The guides' home page; generated reference tables.
            groups.setdefault(page.group, []).append(page)
    lines = ["**Row-Bot features** (by area, from its guides for this version; row_bot_docs reads any of them)"]
    for group, items in groups.items():
        index = next((page for page in items if page.route.rstrip("/") == f"{guides.GUIDES_ROOT}/{group}"), None)
        head = index.title if index else group.replace("-", " ").title()
        about = f": {index.description}" if index and index.description else ""
        others = [page.title for page in items if page is not index]
        lines.append(f"- {head}{about}" + (f" Guides: {'; '.join(others)}." if others else ""))
    return "\n".join(lines)


def _query_health() -> str:
    from row_bot.self_health import describe

    return describe()


def _query_settings() -> str:
    from row_bot.self_settings import describe

    return describe()


def _query_designer() -> str:
    try:
        from row_bot.designer.storage import list_projects
        projects = list_projects()
        if not projects:
            return "**Designer**\nNo projects yet."
        lines = ["**Designer**", f"- {len(projects)} project(s)"]
        for proj in projects[:5]:
            pages = proj.get("page_count", "?")
            ratio = proj.get("aspect_ratio", "?")
            lines.append(f"  - {proj.get('name', 'Untitled')} ({pages} pages, {ratio})")
        if len(projects) > 5:
            lines.append(f"  - … and {len(projects) - 5} more")
        return "\n".join(lines)
    except Exception as exc:
        return f"**Designer**\nError: {exc}"


_QUERY_HANDLERS = {
    "overview": _query_overview,
    "version": _query_version,
    "features": _query_features,
    "model": _query_model,
    "channels": _query_channels,
    "memory": _query_memory,
    "skills": _query_skills,
    "tools": _query_tools,
    "plugins": _query_plugins,
    "mcp": _query_mcp,
    "providers": _query_providers,
    "insights": _query_insights,
    "evolution": _query_evolution,
    "identity": _query_identity,
    "tasks": _query_tasks,
    "agents": _query_agents,
    "agent_profiles": _query_agent_profiles,
    "goals": _query_goals,
    "vision": _query_vision,
    "image_gen": _query_image_gen,
    "video_gen": _query_video_gen,
    "voice": _query_voice,
    "config": _query_config,
    "logs": _query_logs,
    "errors": _query_errors,
    "designer": _query_designer,
    "updates": lambda: _query_updates(),
    "health": _query_health,
    "settings": _query_settings,
    "usage": lambda: _query_usage(),
}


class _StatusQueryInput(BaseModel):
    # Offered straight from the handlers, so the schema and what answers cannot drift apart.
    # What each category reports is in row_bot_status_guide.
    category: Literal[tuple(_QUERY_HANDLERS)] = Field(  # type: ignore[valid-type]
        description="What to query; 'overview' summarises every area."
    )


def _query_updates() -> str:
    """Summary of auto-update state for the agent."""
    try:
        import row_bot.updater as updater
        s = updater.summary_for_status()
    except Exception as exc:  # pragma: no cover - defensive
        return f"**Updates**\nError: {exc}"
    lines = [
        "**Updates**",
        f"- Current version: v{s['current_version']}",
        f"- Channel: {s['channel']}",
        f"- Auto-check: {'on' if s['auto_check'] else 'off'}",
        f"- Download automatically: {'on' if s.get('auto_download', True) else 'off'}",
        f"- Last check: {s.get('last_check') or 'never'}",
    ]
    if s.get("mode") == "external":
        lines.append("- Updates are applied by this install's host")
    elif s.get("dev_install"):
        lines.append("- Dev install — updater disabled")
    if s.get("ready_version"):
        lines.append(f"- Ready: v{s['ready_version']} installs at the next restart (or when Row-Bot quits)")
    elif s.get("phase") in {"downloading", "staging"}:
        lines.append(f"- Preparing the update in the background ({s['phase']})")
    if s["update_available"]:
        lines.append(f"- ⬆ Update available: v{s['available_version']}")
        if s.get("available_notes_summary"):
            lines.append(f"  Summary: {s['available_notes_summary']}")
    else:
        lines.append("- No update available")
    if s.get("skipped_versions"):
        lines.append(f"- Skipped: {', '.join(s['skipped_versions'])}")
    return "\n".join(lines)


def _usage_line(label: str, totals: dict, *, indent: str = "") -> str:
    calls = int(totals.get("calls") or 0)
    if not calls:
        return f"{indent}- {label}: no model calls recorded"
    input_tokens = int(totals.get("input_tokens") or 0)
    output_tokens = int(totals.get("output_tokens") or 0)
    parts = [
        f"{calls:,} call{'s' if calls != 1 else ''}",
        f"{input_tokens + output_tokens:,} tokens ({input_tokens:,} in, {output_tokens:,} out)",
    ]
    if totals.get("cache_read_percent") is not None:
        parts.append(f"{totals['cache_read_percent']}% of input read from cache")
    if totals.get("cost") is not None:
        parts.append(f"${float(totals['cost']):.4f} reported by the provider")
    unreported = int(totals.get("unreported_calls") or 0)
    if unreported:
        parts.append(f"{unreported:,} without reported counts")
    return f"{indent}- {label}: " + " · ".join(parts)


def _query_usage() -> str:
    """Tokens used, from the local usage ledger (counts only, no content)."""
    import time

    from row_bot.providers.usage_ledger import RETENTION_DAYS, usage_summary

    now = time.time()
    lines = [f"**Model Usage** (local ledger of token counts, kept {RETENTION_DAYS} days)"]
    thread_id = _active_runtime_thread_id()
    if thread_id:
        lines.append(_usage_line("This conversation", usage_summary(thread_id=thread_id)))
    for label, days in (("Last 24 hours", 1), ("Last 7 days", 7)):
        lines.append(_usage_line(label, usage_summary(since=now - days * 86_400)))
    month = usage_summary(since=now - 30 * 86_400)
    lines.append(_usage_line("Last 30 days", month))
    if month["by_model"]:
        lines.append("By model (last 30 days):")
        lines.extend(
            _usage_line(f"{row['provider'] or 'unknown'} / {row['model'] or 'unknown'}", row, indent="  ")
            for row in month["by_model"]
        )
    if month["by_purpose"]:
        lines.append("By purpose (last 30 days):")
        lines.extend(_usage_line(row["purpose"], row, indent="  ") for row in month["by_purpose"])
    lines.append(
        "Input includes tokens read from and written to a prompt cache. Costs appear only "
        "where the provider returned one (OpenRouter); Row-Bot keeps no price table."
    )
    return "\n".join(lines)


def _row_bot_status(category: str) -> str:
    """Query Row-Bot's current status and configuration."""
    category = category.strip().lower()
    handler = _QUERY_HANDLERS.get(category)
    if handler is None:
        available = ", ".join(sorted(_QUERY_HANDLERS.keys()))
        return f"Unknown category '{category}'. Available: {available}"
    try:
        return handler()
    except Exception as exc:
        logger.error("row_bot_status query error for '%s': %s", category, exc, exc_info=True)
        return f"Error querying {category}: {exc}"


# ═════════════════════════════════════════════════════════════════════════════
# CHANGES, GUIDES AND FIXES (the self system)
# ═════════════════════════════════════════════════════════════════════════════

def _update_setting(setting: str, value: str) -> str:
    """Change one setting: checked, approved by the chat's approval level, saved with its undo."""
    from row_bot.self_settings import change

    return change(setting, value)


class _DocsInput(BaseModel):
    query: str = Field(default="", description="What the person wants to do or know, in their words.")
    page: str = Field(default="", description="A guide link from an earlier result (/docs/...#section) to read whole.")


_SECTION_CHARS = 1_500
_PAGE_CHARS = 8_000


def _row_bot_docs(query: str = "", page: str = "") -> str:
    """Row-Bot's own guides for this version, read offline."""
    from row_bot import guides

    try:
        if page.strip():
            found = guides.read(page)
            if found is None:
                return f"No guide at {page}. Search with a query instead."
            text = found.text if len(found.text) <= _PAGE_CHARS else found.text[:_PAGE_CHARS].rstrip() + " …"
            return f"### {found.title}\nCite: [{found.title}]({getattr(found, 'link', found.route)})\n\n{text}"
        if not query.strip():
            return "Give a query (what the person wants to do or know) or a guide link as page."
        sections = guides.search(query)
    except guides.GuidesUnavailable as exc:
        return str(exc)
    if not sections:
        return (f"No guide covers '{query}'. For live state use row_bot_status; otherwise say the guides don't "
                "cover it rather than guessing.")
    parts = []
    for section in sections:
        text = section.text if len(section.text) <= _SECTION_CHARS else (
            section.text[:_SECTION_CHARS].rstrip() + f" … (read it whole with page='{section.link}')")
        parts.append(f"### {section.title}\nCite: [{section.title}]({section.link})\n\n{text}")
    parts.append("Answer from these in the guide's words: numbered steps for a how-to, each setting linked to its row "
                 "as the guide or row_bot_status 'settings' gives it (e.g. [Dream Cycle](/settings/preferences#dream-cycle)), "
                 "and the guide cited with its link.")
    return "\n\n".join(parts)


class _FixInput(BaseModel):
    problem: str = Field(description="A problem's id from row_bot_status 'health', or 'all' to check everything again.")


def _row_bot_fix(problem: str) -> str:
    from row_bot.self_health import fix

    return fix(problem)


# ═════════════════════════════════════════════════════════════════════════════
# SKILL CREATION (additive only — never overwrites existing)
# ═════════════════════════════════════════════════════════════════════════════

class _CreateSkillInput(BaseModel):
    name: str = Field(description="Unique snake_case identifier for the skill (e.g. 'weekly_report').")
    display_name: str = Field(description="Human-readable name (e.g. 'Weekly Report').")
    icon: str = Field(description="Single emoji icon (e.g. '📊').")
    description: str = Field(description="One-line description of what the skill does.")
    instructions: str = Field(description="The full skill instructions — step-by-step guidance for the agent.")
    tags: str = Field(default="", description="Comma-separated tags (e.g. 'productivity, writing').")


def _create_skill(
    name: str,
    display_name: str,
    icon: str,
    description: str,
    instructions: str,
    tags: str = "",
) -> str:
    """Create a skill proposal. Applying the proposal performs the mutation."""
    try:
        from row_bot.evolution import build_create_skill_proposal

        proposal = build_create_skill_proposal(
            {
                "name": name,
                "display_name": display_name,
                "icon": icon,
                "description": description,
                "instructions": instructions,
                "tags": tags,
                "enabled": True,
            },
            rationale="Requested through row_bot_create_skill. This records a previewable proposal before any skill file is created.",
        )
        validation = proposal.get("preview", {}).get("validation", {})
        return (
            f"Skill creation proposal created: {proposal['id']}\n"
            f"Status: {proposal.get('status')}\n"
            f"Validation: {json.dumps(validation, ensure_ascii=False)}\n"
            "Preview this proposal, then call row_bot_apply_proposal with the proposal id after user approval."
        )
    except Exception as exc:
        return f"Failed to create skill proposal: {exc}"

# ═════════════════════════════════════════════════════════════════════════════
# SKILL PATCHING (versioned, with user-space override for bundled skills)
# ═════════════════════════════════════════════════════════════════════════════

class _PatchSkillInput(BaseModel):
    name: str = Field(description="The name (identifier) of the skill to patch.")
    updated_instructions: str = Field(
        description="The complete updated instructions text for the skill."
    )
    reason: str = Field(
        description="Brief explanation of why this patch improves the skill."
    )


class _SendFeedbackInput(BaseModel):
    title: str = Field(description="Short feedback title.")
    summary: str = Field(description="Short feedback summary. Secrets and local user paths are redacted before saving.")
    insight_id: str = Field(default="", description="Optional insight id to attach to the feedback proposal.")
    include_logs: bool = Field(default=False, description="Include a small redacted recent warning/error log excerpt.")


class _ProposalApplyInput(BaseModel):
    proposal_id: str = Field(description="The controlled-evolution proposal id to apply.")


class _ProposalRejectInput(BaseModel):
    proposal_id: str = Field(description="The controlled-evolution proposal id to reject.")
    reason: str = Field(default="", description="Why this proposal should not be repeated.")


class _ProposalVerifyInput(BaseModel):
    proposal_id: str = Field(description="The proposal id to mark verified after validation or user confirmation.")
    note: str = Field(default="", description="Short verification note.")


class _CuratorDryRunInput(BaseModel):
    create_proposals: bool = Field(default=True, description="Whether the dry-run should create review proposals. It never mutates skills.")


def _patch_skill(name: str, updated_instructions: str, reason: str) -> str:
    """Create a bounded patch proposal. Applying the proposal performs the mutation."""
    try:
        from row_bot.evolution import build_patch_skill_proposal

        proposal = build_patch_skill_proposal(
            target_skill=name.strip(),
            updated_instructions=updated_instructions,
            reason=reason,
        )
        preview = proposal.get("preview", {})
        validation = preview.get("validation", {})
        return (
            f"Skill patch proposal created: {proposal['id']}\n"
            f"Status: {proposal.get('status')}\n"
            f"Changed lines: {preview.get('changed_lines', 0)}\n"
            f"Validation: {json.dumps(validation, ensure_ascii=False)}\n"
            "Preview the diff, then call row_bot_apply_proposal with the proposal id after user approval."
        )
    except Exception as exc:
        return f"Failed to create skill patch proposal: {exc}"


def _send_feedback(
    title: str,
    summary: str,
    insight_id: str = "",
    include_logs: bool = False,
) -> str:
    """Create a redacted send-feedback proposal without uploading anything."""

    try:
        from row_bot.evolution import build_send_feedback_proposal

        insight = None
        if insight_id:
            try:
                from row_bot.insights import get_insight_by_id

                insight = get_insight_by_id(insight_id)
            except Exception:
                insight = None
        proposal = build_send_feedback_proposal(
            insight,
            title=title,
            summary=summary,
            include_logs=include_logs,
            insight_ids=[insight_id] if insight_id else [],
        )
        return (
            f"Send feedback proposal created: {proposal['id']}\n"
            "Preview the redacted report, then copy it, save it locally, or submit it through the contact page."
        )
    except Exception as exc:
        return f"Failed to create send feedback proposal: {exc}"


def _apply_controlled_proposal(proposal_id: str) -> str:
    """Apply a proposal through the controlled action-run path. The approval card pausing the run and Stop are
    not failures: they go on up."""

    from langgraph.errors import GraphBubbleUp

    try:
        from row_bot.evolution import apply_proposal

        result = apply_proposal(proposal_id, require_approval=True)
        run = result.get("action_run") or {}
        refs = run.get("result_refs") or []
        rollback = run.get("rollback_ref") or ""
        details = [result.get("message", "Proposal applied.")]
        if refs:
            details.append("Result refs: " + ", ".join(str(ref) for ref in refs))
        if rollback:
            details.append(f"Rollback ref: {rollback}")
        return "\n".join(details)
    except (GraphBubbleUp, InterruptedError):
        raise
    except Exception as exc:
        return f"Failed to apply proposal: {exc}"


def _reject_controlled_proposal(proposal_id: str, reason: str = "") -> str:
    try:
        from row_bot.evolution import reject_proposal

        proposal = reject_proposal(proposal_id, reason)
        return f"Proposal rejected: {proposal.get('title')} ({proposal_id}). Rejection memory recorded."
    except Exception as exc:
        return f"Failed to reject proposal: {exc}"


def _verify_controlled_proposal(proposal_id: str, note: str = "") -> str:
    try:
        from row_bot.evolution import mark_proposal_verified

        if mark_proposal_verified(proposal_id, note):
            return f"Proposal verified: {proposal_id}"
        return f"Proposal not found or could not be verified: {proposal_id}"
    except Exception as exc:
        return f"Failed to verify proposal: {exc}"


def _curator_dry_run(create_proposals: bool = True) -> str:
    try:
        from row_bot.evolution import review_skill_library_dry_run

        report = review_skill_library_dry_run(create_proposals=create_proposals)
        summary = report.get("summary", {})
        return (
            f"Curator dry-run complete: {report.get('id')}\n"
            f"Manual skills: {summary.get('manual_skill_count', 0)}\n"
            f"Findings: {summary.get('finding_count', 0)}\n"
            f"Proposals: {summary.get('proposal_count', 0)}\n"
            f"Mutated skills: {report.get('mutated_skills')}"
        )
    except Exception as exc:
        return f"Curator dry-run failed: {exc}"


# ═════════════════════════════════════════════════════════════════════════════
# TOOL CLASS
# ═════════════════════════════════════════════════════════════════════════════

class RowBotStatusTool(BaseTool):

    @property
    def name(self) -> str:
        return "row_bot_status"

    @property
    def display_name(self) -> str:
        return "🪞 Row-Bot Status"

    @property
    def description(self) -> str:
        return (
            "Row-Bot's own state, guides, settings and health: what it is running now, how to do things in "
            "Row-Bot, changing its settings with approval, and what needs attention."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def destructive_tool_names(self) -> set[str]:
        # These sub-tools perform their own interrupt() calls with specific
        # labels and arguments. Listing them here would wrap them in a second,
        # generic approval gate before the real tool logic runs.
        return set()

    def as_langchain_tools(self) -> list:
        tools = [
            StructuredTool.from_function(
                func=_row_bot_status,
                name="row_bot_status",
                description=(
                    "Row-Bot's live state, one area at a time: version, model and readiness, providers and "
                    "sign-ins, tools, skills, the settings Row-Bot can change ('settings'), what needs "
                    "attention ('health'), and more."
                ),
                args_schema=_StatusQueryInput,
            ),
            StructuredTool.from_function(
                func=_row_bot_docs,
                name="row_bot_docs",
                description=(
                    "Search Row-Bot's own guides for this version, offline: how to do something in Row-Bot, "
                    "what a feature or setting does, or where it is. Cite the guide and link each setting."
                ),
                args_schema=_DocsInput,
            ),
            StructuredTool.from_function(
                func=_update_setting,
                name="row_bot_update_setting",
                description=(
                    "Change one of the settings row_bot_status 'settings' lists. It is checked first; the "
                    "chat's approval level decides who approves, and the person can undo it."
                ),
                args_schema=_SettingUpdateInput,
            ),
            StructuredTool.from_function(
                func=_row_bot_fix,
                name="row_bot_fix",
                description=(
                    "Check again for what needs attention (row_bot_status 'health'), or get one problem's fix: "
                    "Row-Bot checks again itself and gives the person the link for fixes only they can do."
                ),
                args_schema=_FixInput,
            ),
        ]

        # Self-improvement tools: only while "Learn new skills" is on.
        from row_bot.identity import is_self_improvement_enabled

        if is_self_improvement_enabled():
            tools.append(StructuredTool.from_function(
                func=_create_skill,
                name="row_bot_create_skill",
                description=(
                    "Create a proposal for a new user skill (reusable instruction pack). "
                    "Additive only: it never overwrites a skill. Apply the proposal separately, "
                    "after the person has previewed and approved it."
                ),
                args_schema=_CreateSkillInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_patch_skill,
                name="row_bot_patch_skill",
                description=(
                    "Create a bounded patch-skill proposal with a diff preview. "
                    "Does not mutate skills; apply the proposal separately after approval. "
                    "Tool guides cannot be patched through this path."
                ),
                args_schema=_PatchSkillInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_send_feedback,
                name="row_bot_send_feedback",
                description=(
                    "Create a redacted send-feedback proposal. Applying it saves a local "
                    "markdown report; the user can copy it or submit it through the "
                    "Row-Bot contact page."
                ),
                args_schema=_SendFeedbackInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_apply_controlled_proposal,
                name="row_bot_apply_proposal",
                description=(
                    "Apply a controlled self-evolution proposal. Mutating proposal types "
                    "are approval-gated, audited as ActionRun records, and skill patches "
                    "include rollback refs."
                ),
                args_schema=_ProposalApplyInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_reject_controlled_proposal,
                name="row_bot_reject_proposal",
                description=(
                    "Reject a controlled self-evolution proposal and record feedback so "
                    "similar future proposals can account for the rejection."
                ),
                args_schema=_ProposalRejectInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_verify_controlled_proposal,
                name="row_bot_verify_proposal",
                description=(
                    "Mark an applied proposal verified after explicit validation or user confirmation."
                ),
                args_schema=_ProposalVerifyInput,
            ))
            tools.append(StructuredTool.from_function(
                func=_curator_dry_run,
                name="row_bot_review_skill_library",
                description=(
                    "Run a manual dry-run review of the skill library. It reports findings "
                    "and may create proposals, but never mutates skill files."
                ),
                args_schema=_CuratorDryRunInput,
            ))

        return tools

    def execute(self, query: str) -> str:
        return _row_bot_status(query)


registry.register(RowBotStatusTool())
