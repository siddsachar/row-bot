"""Slash command registry and composer token helpers.

This module is intentionally UI-light. It owns command metadata, generated
manual-skill commands, collision rules, text filtering, and send-path command
dispatch that can run without UI widgets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Literal


ArgumentBehavior = Literal["none", "optional", "required", "prefix"]


@dataclass(frozen=True)
class SlashCommandSpec:
    id: str
    slash: str
    aliases: tuple[str, ...]
    title: str
    description: str
    icon: str
    category: str
    argument_behavior: ArgumentBehavior
    handler_key: str
    skill_name: str = ""

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.slash, *self.aliases)


BUILTIN_COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec(
        "skills", "/skills", ("/skill",), "Skills",
        "Open the Skills picker for this chat.", "auto_fix_high",
        "Skills", "none", "open_skills",
    ),
    SlashCommandSpec(
        "skill-reset", "/skill-reset", ("/skill reset",), "Reset Skills",
        "Reset Smart Skills for this chat.", "restart_alt",
        "Skills", "none", "skill_reset",
    ),
    SlashCommandSpec(
        "noskill", "/noskill", (), "Remove Skill",
        "Remove or prepare to disable a Smart Skill in this chat.", "remove_circle",
        "Skills", "prefix", "noskill",
    ),
    SlashCommandSpec(
        "new", "/new", (), "New Chat",
        "Start a new conversation thread.", "add_comment",
        "Chat", "none", "new_thread",
    ),
    SlashCommandSpec(
        "stop", "/stop", (), "Stop",
        "Stop the current generation if one is running.", "stop_circle",
        "Chat", "none", "stop_generation",
    ),
    SlashCommandSpec(
        "reasoning", "/reasoning", (), "Reasoning",
        "Show or set reasoning for the active model in this chat.", "psychology",
        "Chat", "prefix", "reasoning",
    ),
    SlashCommandSpec(
        "profiles", "/profiles", ("/agent-profiles",), "Agents",
        "List the agents this chat can use.", "badge",
        "Agents", "optional", "profiles",
    ),
    SlashCommandSpec(
        "profile", "/profile", ("/agent-profile",), "Agent",
        "Show or set the agent for this chat.", "person_pin",
        "Agents", "prefix", "profile",
    ),
    SlashCommandSpec(
        "agents", "/agents", (), "Agent runs",
        "Show the agent runs in this chat.", "hub",
        "Agents", "optional", "agents",
    ),
    SlashCommandSpec(
        "agent", "/agent", ("/subagent",), "Start Agent",
        "Start a child Agent with an optional profile or --worktree isolation.", "hub",
        "Agents", "prefix", "agent",
    ),
    SlashCommandSpec(
        "goal", "/goal", (), "Goal",
        "Start or control a durable thread goal.", "flag",
        "Agents", "prefix", "goal",
    ),
    SlashCommandSpec(
        "status", "/status", (), "Status",
        "Show a lightweight local status summary.", "monitor_heart",
        "Info", "none", "status",
    ),
    SlashCommandSpec(
        "tools", "/tools", (), "Tools",
        "Show enabled tools read-only.", "construction",
        "Info", "none", "tools",
    ),
    SlashCommandSpec(
        "export", "/export", (), "Export",
        "Open export for the current thread.", "download",
        "App", "none", "export",
    ),
    SlashCommandSpec(
        "help", "/help", (), "Help",
        "Show available slash commands.", "help",
        "Info", "none", "help",
    ),
)


def normalize_command_name(value: str) -> str:
    """Normalize a command or skill label to a slash-token suffix."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def normalize_slash(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    head, *tail = text.split(maxsplit=1)
    head = "/" + normalize_command_name(head[1:])
    return f"{head} {tail[0].strip()}" if tail else head


def _skill_aliases(skill) -> tuple[str, ...]:
    aliases: list[str] = []
    for raw in (getattr(skill, "name", ""), getattr(skill, "display_name", "")):
        token = normalize_command_name(raw)
        if token:
            aliases.append("/" + token)
        if "_" in str(raw or ""):
            aliases.append("/" + str(raw).strip().lower())
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        normalized = normalize_slash(alias)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _passive_manual_skills() -> list:
    """Read enabled manual skills without populating or migrating runtime state."""

    try:
        import row_bot.skills as skills

        snapshot = skills.read_client_skills()
        manual = [
            item["skill"]
            for name, item in snapshot["items"].items()
            if snapshot["enabled"].get(name, False)
            and not skills.is_tool_guide(item["skill"])
        ]
        return sorted(manual, key=lambda skill: (skill.display_name.casefold(), skill.name))
    except Exception:
        return []


def _generated_skill_commands(
    reserved: set[str],
    manual_skills: Iterable | None = None,
) -> list[SlashCommandSpec]:
    manual = list(manual_skills) if manual_skills is not None else _passive_manual_skills()

    specs: list[SlashCommandSpec] = []
    used = set(reserved)
    for skill in manual:
        aliases = _skill_aliases(skill)
        available_aliases = tuple(alias for alias in aliases if alias not in used)
        if not available_aliases:
            continue
        primary = available_aliases[0]
        used.update(available_aliases)
        specs.append(SlashCommandSpec(
            id=f"skill:{skill.name}",
            slash=primary,
            aliases=available_aliases[1:],
            title=getattr(skill, "display_name", skill.name),
            description=getattr(skill, "description", "") or "Activate this skill for the current chat.",
            icon=getattr(skill, "icon", "*") or "*",
            category="Skills",
            argument_behavior="none",
            handler_key="activate_skill",
            skill_name=skill.name,
        ))
    return specs


def get_builtin_commands() -> list[SlashCommandSpec]:
    return list(BUILTIN_COMMANDS)


def get_command_specs(
    *,
    include_skills: bool = True,
    manual_skills: Iterable | None = None,
) -> list[SlashCommandSpec]:
    """Return canonical commands, using a passive skill snapshot by default."""

    builtins = get_builtin_commands()
    reserved = {normalize_slash(name) for spec in builtins for name in spec.all_names}
    if not include_skills:
        return builtins
    return [*builtins, *_generated_skill_commands(reserved, manual_skills)]


def build_lookup(*, include_skills: bool = True) -> dict[str, SlashCommandSpec]:
    lookup: dict[str, SlashCommandSpec] = {}
    for spec in get_command_specs(include_skills=include_skills):
        for name in spec.all_names:
            lookup.setdefault(normalize_slash(name), spec)
    return lookup


def resolve_command_token(token: str, *, include_skills: bool = True) -> SlashCommandSpec | None:
    return build_lookup(include_skills=include_skills).get(normalize_slash(token))


def filter_command_specs(
    specs: Iterable[SlashCommandSpec],
    query: str,
    *,
    limit: int = 12,
) -> list[SlashCommandSpec]:
    q = normalize_command_name(query)
    if not q:
        return list(specs)[:limit]

    def _subsequence_score(needle: str, haystack: str) -> int | None:
        if not needle:
            return 0
        pos = -1
        gaps = 0
        first = -1
        for char in needle:
            next_pos = haystack.find(char, pos + 1)
            if next_pos < 0:
                return None
            if first < 0:
                first = next_pos
            if pos >= 0:
                gaps += max(0, next_pos - pos - 1)
            pos = next_pos
        return first + gaps + max(0, len(haystack) - len(needle)) // 8

    scored: list[tuple[int, int, SlashCommandSpec]] = []
    for index, spec in enumerate(specs):
        fields: list[tuple[int, str]] = []
        for name_index, name in enumerate(spec.all_names):
            fields.append((0 if name_index == 0 else 2, normalize_command_name(name.lstrip("/"))))
        fields.extend(
            [
                (6, normalize_command_name(spec.title)),
                (12, normalize_command_name(spec.category)),
                (20, normalize_command_name(spec.description)),
            ]
        )
        best: int | None = None
        for weight, haystack in fields:
            if not haystack:
                continue
            score: int | None
            if haystack == q:
                score = weight
            elif haystack.startswith(q):
                score = 10 + weight + len(haystack) - len(q)
            elif q in haystack:
                score = 30 + weight + haystack.index(q)
            else:
                fuzzy = _subsequence_score(q, haystack)
                score = None if fuzzy is None else 60 + weight + fuzzy + len(haystack)
            if score is not None:
                best = score if best is None else min(best, score)
        if best is not None:
            scored.append((best, index, spec))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def argument_hint(spec: SlashCommandSpec) -> str:
    return {
        "none": "",
        "optional": "Optional argument",
        "required": "Argument required",
        "prefix": "Type details after the command",
    }.get(spec.argument_behavior, "")


def help_text(*, include_skills: bool = True) -> str:
    grouped: dict[str, list[SlashCommandSpec]] = {}
    for spec in get_command_specs(include_skills=include_skills):
        grouped.setdefault(spec.category, []).append(spec)
    category_order = ["Chat", "Skills", "Agents", "Info", "App"]
    lines = ["Available slash commands"]
    for category in category_order:
        specs = grouped.pop(category, [])
        if not specs:
            continue
        lines.extend(["", f"**{category}**", ""])
        for spec in specs:
            label = spec.title if spec.skill_name else spec.description
            lines.append(f"- `{spec.slash}` - {label}")
    for category, specs in grouped.items():
        lines.extend(["", f"**{category}**", ""])
        for spec in specs:
            label = spec.title if spec.skill_name else spec.description
            lines.append(f"- `{spec.slash}` - {label}")
    return "\n".join(lines)


def with_skill_name(spec: SlashCommandSpec, skill_name: str) -> SlashCommandSpec:
    """Small test helper for collision scenarios."""
    return replace(spec, skill_name=skill_name)
