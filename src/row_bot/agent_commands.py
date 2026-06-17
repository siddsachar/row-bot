"""Shared text handlers for Agent slash/channel commands."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable


PROFILE_CLEAR_TOKENS = {"clear", "default", "none", "off", "reset"}
ACTIVE_AGENT_STATUSES = {"queued", "running", "waiting_approval", "waiting_user", "paused"}
DEFAULT_DIRECT_AGENT_PROFILE = "worker"


def _profile_skills_override(profile: dict | None) -> list[str]:
    if not profile:
        return []
    skill_policy = profile.get("skill_policy_json") or {}
    if not isinstance(skill_policy, dict):
        return []
    skills: list[str] = []
    seen: set[str] = set()
    for item in skill_policy.get("skills_override") or []:
        name = str(item or "").strip()
        if name and name not in seen:
            seen.add(name)
            skills.append(name)
    return skills


@dataclass(frozen=True)
class AgentSpawnRequest:
    """A user-explicit request to start a child Agent directly."""

    objective: str
    profile: str = DEFAULT_DIRECT_AGENT_PROFILE
    explicit_profile: bool = False
    source: str = "natural"


_DIRECT_AGENT_VERBS = {"use", "create", "spawn", "start", "launch", "make"}
_DIRECT_AGENT_MARKERS = (" to ", " for ", " about ", " on ")
_AGENT_NOUN_RE = re.compile(r"^(?:an?\s+|another\s+|new\s+)?(?:child\s+)?(?:subagent|agent)\b")


def _normalize_words(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _profile_match_candidates() -> list[tuple[str, str]]:
    from row_bot.agent_profiles import list_agent_profiles

    candidates: list[tuple[str, str]] = []
    for profile in list_agent_profiles(enabled_only=True, include_builtins=True):
        slug = str(profile.get("slug") or "").strip()
        if not slug:
            continue
        names = {
            slug,
            slug.replace("_", " "),
            slug.replace("-", " "),
            str(profile.get("display_name") or "").strip(),
        }
        for name in names:
            normalized = _normalize_words(name)
            if normalized:
                candidates.append((normalized, slug))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _consume_leading_profile(text: str) -> tuple[str, str] | None:
    normalized_text = _normalize_words(text)
    if not normalized_text:
        return None
    for normalized_name, slug in _profile_match_candidates():
        if normalized_text == normalized_name or normalized_text.startswith(normalized_name + " "):
            words_to_drop = len(normalized_name.split())
            raw_words = str(text or "").strip().split()
            return slug, " ".join(raw_words[words_to_drop:]).strip()
    return None


def _strip_task_marker(text: str) -> str:
    raw = str(text or "").strip()
    lower = raw.lower()
    for marker in _DIRECT_AGENT_MARKERS:
        if lower.startswith(marker.strip() + " "):
            return raw[len(marker.strip()):].strip()
    return raw


def _parse_natural_agent_request(text: str) -> AgentSpawnRequest | None:
    raw = " ".join(str(text or "").strip().split())
    if not raw or raw.startswith("/"):
        return None
    raw = re.sub(r"^(?:please\s+)", "", raw, flags=re.IGNORECASE).strip()
    lower = raw.lower()
    head, _, tail = lower.partition(" ")
    if head not in _DIRECT_AGENT_VERBS and not lower.startswith("delegate "):
        return None
    rest = raw[len(head):].strip() if head in _DIRECT_AGENT_VERBS else raw[len("delegate"):].strip()
    rest = re.sub(r"^(?:please\s+)?", "", rest, flags=re.IGNORECASE).strip()

    profile_rest = re.sub(
        r"^(?:an?\s+|another\s+|new\s+)",
        "",
        rest,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    profile_match = _consume_leading_profile(profile_rest)
    if profile_match:
        profile_slug, after_profile = profile_match
        after_profile = re.sub(
            r"^(?:child\s+)?(?:subagent|agent)\b",
            "",
            after_profile,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        objective = _strip_task_marker(after_profile)
        if objective:
            return AgentSpawnRequest(
                objective=objective,
                profile=profile_slug,
                explicit_profile=True,
                source="natural",
            )

    agent_match = _AGENT_NOUN_RE.match(rest.lower())
    if agent_match:
        objective = _strip_task_marker(rest[agent_match.end():].strip())
        if objective:
            return AgentSpawnRequest(
                objective=objective,
                profile=DEFAULT_DIRECT_AGENT_PROFILE,
                explicit_profile=False,
                source="natural",
            )

    if lower.startswith("delegate "):
        objective = _strip_task_marker(rest)
        if objective:
            return AgentSpawnRequest(
                objective=objective,
                profile=DEFAULT_DIRECT_AGENT_PROFILE,
                explicit_profile=False,
                source="natural",
            )

    return None


def is_agent_spawn_command(text: str) -> bool:
    value = str(text or "").strip().lower()
    return value in {"/agent", "/subagent"} or value.startswith("/agent ") or value.startswith("/subagent ")


def parse_agent_spawn_text(text: str) -> AgentSpawnRequest | None:
    """Parse explicit direct child-Agent requests without task-based routing."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if is_agent_spawn_command(raw):
        arg = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) > 1 else ""
        if not arg:
            return None
        profile_match = _consume_leading_profile(arg)
        if profile_match:
            profile_slug, objective = profile_match
            objective = _strip_task_marker(objective)
            if objective:
                return AgentSpawnRequest(
                    objective=objective,
                    profile=profile_slug,
                    explicit_profile=True,
                    source="slash",
                )
        return AgentSpawnRequest(
            objective=arg,
            profile=DEFAULT_DIRECT_AGENT_PROFILE,
            explicit_profile=False,
            source="slash",
        )
    return _parse_natural_agent_request(raw)


def format_agent_spawn_usage() -> str:
    return (
        "Usage: `/agent [profile] <task>`.\n\n"
        "Examples:\n"
        "- `/agent write a 600 word essay and save it as ai_agent_smoke.pdf`\n"
        "- `/agent reviewer review the latest diff for regressions`\n\n"
        "Generic Agent requests use `worker`. A specialized profile is used only "
        "when you explicitly name an enabled Agent Profile."
    )


def agent_spawn_display_name(request: AgentSpawnRequest) -> str:
    profile = str(request.profile or DEFAULT_DIRECT_AGENT_PROFILE).strip()
    objective = " ".join(str(request.objective or "").split())
    title = objective[:42].rstrip()
    if len(objective) > 42:
        title += "..."
    if not title:
        return "Agent"
    if profile == DEFAULT_DIRECT_AGENT_PROFILE:
        return f"Agent: {title}"
    return f"{profile.replace('_', ' ').title()}: {title}"


def spawn_agent_from_request(
    thread_id: str | None,
    request: AgentSpawnRequest,
    *,
    enabled_tool_names: Iterable[str] | None = None,
) -> dict:
    """Start a direct child Agent Run from a parsed user request."""
    if not thread_id:
        raise ValueError("Direct Agent requests require a parent thread.")
    from row_bot.agent_runner import spawn_agent_run

    return spawn_agent_run(
        request.objective,
        parent_thread_id=str(thread_id),
        profile=request.profile or DEFAULT_DIRECT_AGENT_PROFILE,
        display_name=agent_spawn_display_name(request),
        context_mode="auto",
        enabled_tool_names=list(enabled_tool_names or []),
        wait=False,
    )


def format_agent_spawn_started(run: dict, request: AgentSpawnRequest) -> str:
    run_id = str((run or {}).get("id") or "").strip()
    status = str((run or {}).get("status") or "queued").strip() or "queued"
    name = str((run or {}).get("display_name") or agent_spawn_display_name(request)).strip()
    profile = str((run or {}).get("profile_slug") or request.profile or DEFAULT_DIRECT_AGENT_PROFILE).strip()
    suffix = f" (`{run_id}`)" if run_id else ""
    return f"Started Agent **{name}** with profile `{profile}`. Status: `{status}`{suffix}."


def _profile_lines(query: str = "", *, limit: int = 18) -> list[str]:
    from row_bot.agent_profiles import list_agent_profiles, normalize_profile_slug, profile_summary

    q = normalize_profile_slug(query)
    profiles = list_agent_profiles(enabled_only=True, include_builtins=True)
    if q:
        profiles = [
            profile for profile in profiles
            if q in normalize_profile_slug(
                " ".join(
                    [
                        str(profile.get("slug") or ""),
                        str(profile.get("display_name") or ""),
                        str(profile.get("description") or ""),
                        str(profile.get("when_to_use") or ""),
                    ]
                )
            )
        ]
    profiles.sort(
        key=lambda item: (
            0 if item.get("source") == "builtin" else 1,
            str(item.get("display_name") or item.get("slug") or "").lower(),
        )
    )
    return [f"- `{profile['slug']}` - {profile_summary(profile)}" for profile in profiles[:limit]]


def format_agent_profiles(query: str = "", *, limit: int = 18) -> str:
    """Return a Markdown list of selectable Agent Profiles."""
    lines = ["**Agent Profiles**"]
    matches = _profile_lines(query, limit=limit)
    if not matches:
        lines.append(f"No enabled Agent Profiles matched `{query}`.")
    else:
        lines.extend(matches)
    lines.append("")
    lines.append("Use `/profile <slug>` to select one for the current thread.")
    lines.append("Use `/profile clear` to return to normal Row-Bot behavior.")
    return "\n".join(lines)


def _current_profile_text(thread_id: str) -> str:
    from row_bot.agent_profiles import get_agent_profile
    from row_bot.threads import _get_thread_agent_profile

    pointer = _get_thread_agent_profile(thread_id)
    profile_ref = pointer.get("id") or pointer.get("slug")
    if not profile_ref:
        return (
            "Current Agent Profile: normal Row-Bot behavior "
            "(`row_bot_default`). Use `/profiles` to browse choices."
        )
    profile = get_agent_profile(profile_ref, enabled_only=False)
    if profile is None:
        return (
            "Current Agent Profile reference is missing: "
            f"`{profile_ref}`. Use `/profile clear` or `/profile <slug>`."
        )
    if not profile.get("enabled", True):
        return (
            "Current Agent Profile is disabled: "
            f"`{profile.get('slug') or profile_ref}`. Use `/profile clear` "
            "or `/profile <slug>`."
        )
    return (
        "Current Agent Profile: "
        f"**{profile.get('display_name') or profile.get('slug')}** "
        f"(`{profile.get('slug')}`)."
    )


def handle_thread_profile_command(thread_id: str | None, arg: str = "") -> str:
    """Show, set, or clear the Agent Profile for one conversation thread."""
    if not thread_id:
        return "Could not identify the current conversation thread."
    raw = str(arg or "").strip()
    if not raw:
        return _current_profile_text(thread_id)
    if raw.lower() in PROFILE_CLEAR_TOKENS:
        from row_bot.agent import clear_agent_cache
        from row_bot.threads import _clear_thread_agent_profile, set_thread_skills_override

        _clear_thread_agent_profile(thread_id)
        set_thread_skills_override(thread_id, None)
        clear_agent_cache()
        return "Agent Profile cleared for this thread. Normal Row-Bot behavior will be used."

    from row_bot.agent import clear_agent_cache
    from row_bot.agent_profiles import AgentProfileError, require_agent_profile
    from row_bot.threads import _set_thread_agent_profile, set_thread_skills_override

    try:
        profile = require_agent_profile(raw, enabled_only=True)
        stored = _set_thread_agent_profile(thread_id, profile["id"])
        profile_skills = _profile_skills_override(profile)
        set_thread_skills_override(thread_id, profile_skills or None)
    except AgentProfileError as exc:
        return f"Could not set Agent Profile `{raw}`: {exc}\n\n{format_agent_profiles(raw, limit=8)}"
    clear_agent_cache()
    return (
        "Agent Profile set for this thread: "
        f"**{profile.get('display_name') or profile.get('slug')}** "
        f"(`{stored.get('slug')}`)."
    )


def _status_counts(runs: Iterable[dict]) -> str:
    counts = Counter(str(run.get("status") or "unknown") for run in runs)
    if not counts:
        return ""
    return ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))


def format_agents_status(
    *,
    parent_thread_id: str | None = None,
    include_all: bool = False,
    limit: int = 10,
) -> str:
    """Return a compact Markdown summary of Agent Runs."""
    from row_bot.agent_runs import list_agent_runs

    thread_filter = None if include_all else parent_thread_id
    runs = list_agent_runs(parent_thread_id=thread_filter, limit=limit)
    scope = "all threads" if include_all or not parent_thread_id else f"thread `{parent_thread_id}`"
    lines = [f"**Agents** ({scope})"]
    if not runs:
        lines.append("No Agent Runs yet.")
        return "\n".join(lines)

    active = [run for run in runs if str(run.get("status") or "") in ACTIVE_AGENT_STATUSES]
    lines.append(f"Showing {len(runs)} most recent. Active: {len(active)}. {_status_counts(runs)}")
    lines.append("")
    for run in runs:
        title = str(run.get("display_name") or run.get("id") or "Agent")
        status = str(run.get("status") or "unknown")
        profile = str(run.get("profile_display_name") or run.get("profile_slug") or "Unprofiled")
        message = str(run.get("status_message") or run.get("summary") or run.get("error") or "").strip()
        suffix = f" - {message}" if message else ""
        lines.append(f"- `{run.get('id')}` {title} - {status} - {profile}{suffix}")
    return "\n".join(lines)
