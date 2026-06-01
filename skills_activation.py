"""Chat-level Smart Skills activation state and deterministic suggestions.

This module intentionally does not know how to inject tool guides. Tool guides
remain owned by :mod:`skills` and are activated from enabled tools there.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(os.environ.get("THOTH_DATA_DIR", pathlib.Path.home() / ".thoth"))
STATE_PATH = DATA_DIR / "skills_activation.json"
MAX_TRACES = 200
_STOPWORDS = {
    "about", "after", "all", "and", "are", "but", "can", "for", "from",
    "has", "have", "into", "more", "not", "please", "should", "that",
    "the", "then", "these", "this", "those", "use", "using", "when",
    "will", "with", "you", "your",
}


@dataclass(frozen=True)
class SkillCommand:
    action: str
    name: str = ""


@dataclass(frozen=True)
class SuggestedSkill:
    name: str
    display_name: str
    icon: str
    description: str
    reason: str
    score: float


@dataclass(frozen=True)
class SkillActivationSnapshot:
    thread_id: str
    active: list[str]
    pinned: list[str]
    disabled: list[str]
    dismissed: list[str]
    smart_off: bool
    suggestions: list[SuggestedSkill]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_store() -> dict:
    return {
        "version": 1,
        "threads": {},
        "telemetry": {"skills": {}, "traces": []},
    }


def _load_store() -> dict:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("threads", {})
                data.setdefault("telemetry", {})
                data["telemetry"].setdefault("skills", {})
                data["telemetry"].setdefault("traces", [])
                return data
    except Exception:
        logger.debug("Failed to load Smart Skills activation state", exc_info=True)
    return _default_store()


def _save_store(store: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".skills_activation.", suffix=".json", dir=str(DATA_DIR)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_name, STATE_PATH)
    except Exception:
        logger.debug("Failed to save Smart Skills activation state", exc_info=True)


def _thread_state(store: dict, thread_id: str) -> dict:
    threads = store.setdefault("threads", {})
    state = threads.setdefault(str(thread_id or "default"), {})
    state.setdefault("pinned", [])
    state.setdefault("disabled", [])
    state.setdefault("dismissed", [])
    state.setdefault("smart_off", False)
    return state


def _ordered_unique(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9_ -]+", " ", str(text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.split(r"[\s_\-/.]+", _normalize(text))
        if len(tok) >= 3 and tok not in _STOPWORDS
    }


def _phrase_matches(query_norm: str, phrase: str) -> bool:
    phrase_norm = _normalize(phrase)
    return bool(phrase_norm and phrase_norm in query_norm)


def _all_manual_skills() -> list:
    import skills

    if not skills.skills_loaded():
        skills.load_skills()
    return [skill for skill in skills.get_manual_skills() if not skills.is_tool_guide(skill)]


def _available_manual_skills() -> list:
    import skills

    return [skill for skill in _all_manual_skills() if skills.is_enabled(skill.name)]


def resolve_skill_name(raw_name: str) -> tuple[str | None, str | None]:
    """Resolve a command argument to a manual skill name.

    Returns ``(name, error)``. Matching is exact first, then case-insensitive
    name/display name, then a single substring match.
    """
    query = _normalize(raw_name)
    if not query:
        return None, "Skill name is required."
    manual = _all_manual_skills()
    available = {skill.name for skill in _available_manual_skills()}
    by_exact = {skill.name: skill.name for skill in manual}
    if raw_name in by_exact:
        if raw_name in available:
            return raw_name, None
        return None, f"Skill is off in the Skills library: {raw_name}"
    folded: dict[str, str] = {}
    for skill in manual:
        folded[_normalize(skill.name)] = skill.name
        folded[_normalize(skill.display_name)] = skill.name
    if query in folded:
        name = folded[query]
        if name in available:
            return name, None
        return None, f"Skill is off in the Skills library: {name}"
    matches = [
        skill.name
        for skill in manual
        if query in _normalize(skill.name) or query in _normalize(skill.display_name)
    ]
    if len(matches) == 1:
        if matches[0] in available:
            return matches[0], None
        return None, f"Skill is off in the Skills library: {matches[0]}"
    if matches:
        unavailable_matches = [name for name in matches if name not in available]
        available_matches = [name for name in matches if name in available]
        if len(available_matches) == 1 and not unavailable_matches:
            return available_matches[0], None
        if len(available_matches) == 1 and unavailable_matches:
            return available_matches[0], None
        if available_matches:
            return None, "Multiple skills match: " + ", ".join(available_matches[:6])
        return None, "Skill is off in the Skills library: " + ", ".join(unavailable_matches[:6])
    return None, f"Skill not found: {raw_name}"


def parse_skill_command(text: str) -> SkillCommand | None:
    text = str(text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()
    if cmd == "/skills":
        return SkillCommand("list")
    if cmd == "/noskill":
        return SkillCommand("disable", parts[1].strip() if len(parts) > 1 else "")
    if cmd != "/skill":
        return None
    if len(parts) == 1:
        return SkillCommand("list")
    arg = parts[1].strip()
    lowered = arg.lower()
    if lowered == "off":
        return SkillCommand("off")
    if lowered == "reset":
        return SkillCommand("reset")
    if lowered == "once":
        return SkillCommand("unsupported_once")
    name = text.split(maxsplit=1)[1].strip()
    return SkillCommand("pin", name)


def apply_skill_command(
    thread_id: str,
    text: str,
    *,
    current_text: str = "",
    enabled_tool_names: Iterable[str] | None = None,
) -> str | None:
    command = parse_skill_command(text)
    if command is None:
        return None
    if command.action == "list":
        snap = get_activation_snapshot(
            thread_id,
            current_text=current_text,
            enabled_tool_names=enabled_tool_names,
        )
        lines = ["Skills for this chat:"]
        lines.append("Suggestions: off" if snap.smart_off else "Suggestions: on")
        lines.append("Active: " + (", ".join(snap.active) if snap.active else "none"))
        if snap.disabled:
            lines.append("Disabled here: " + ", ".join(snap.disabled))
        if snap.suggestions:
            lines.append(
                "Suggested: "
                + ", ".join(f"{s.display_name} ({s.reason})" for s in snap.suggestions)
            )
        return "\n".join(lines)
    if command.action == "off":
        set_smart_off(thread_id, True)
        return "Skill suggestions are off for this chat. Active skills stay active until removed."
    if command.action == "reset":
        reset_thread(thread_id)
        return "Skills reset for this chat."
    if command.action == "unsupported_once":
        return "Temporary skill activation is not supported. Use /skill <name> to activate a skill for this chat, then remove it when done."

    name, error = resolve_skill_name(command.name)
    if error:
        return error
    assert name is not None
    if command.action == "pin":
        pin_skill(thread_id, name)
        record_accept(thread_id, name, source="slash")
        return f"Skill active for this chat: {name}"
    if command.action == "disable":
        disable_skill(thread_id, name)
        return f"Skill disabled for this chat: {name}"
    return None


def pin_skill(thread_id: str, skill_name: str) -> None:
    store = _load_store()
    state = _thread_state(store, thread_id)
    state["pinned"] = _ordered_unique([*state.get("pinned", []), skill_name])
    state["disabled"] = [n for n in state.get("disabled", []) if n != skill_name]
    state["dismissed"] = [n for n in state.get("dismissed", []) if n != skill_name]
    _save_store(store)


def disable_skill(thread_id: str, skill_name: str) -> None:
    store = _load_store()
    state = _thread_state(store, thread_id)
    state["disabled"] = _ordered_unique([*state.get("disabled", []), skill_name])
    state["pinned"] = [n for n in state.get("pinned", []) if n != skill_name]
    _save_store(store)


def dismiss_suggestion(thread_id: str, skill_name: str) -> None:
    store = _load_store()
    state = _thread_state(store, thread_id)
    state["dismissed"] = _ordered_unique([*state.get("dismissed", []), skill_name])
    _save_store(store)
    record_dismiss(thread_id, skill_name, source="ui")


def set_smart_off(thread_id: str, value: bool) -> None:
    store = _load_store()
    _thread_state(store, thread_id)["smart_off"] = bool(value)
    _save_store(store)


def reset_thread(thread_id: str) -> None:
    store = _load_store()
    store.setdefault("threads", {}).pop(str(thread_id or "default"), None)
    _save_store(store)


def resolve_active_skill_names(
    thread_id: str,
    *,
    explicit_override: list[str] | None = None,
    is_background: bool = False,
) -> list[str]:
    """Return manual skill names to inject for this turn."""
    manual_names = {skill.name for skill in _available_manual_skills()}
    if is_background:
        return [n for n in (explicit_override or []) if n in manual_names]
    store = _load_store()
    state = _thread_state(store, thread_id)
    disabled = set(state.get("disabled", []))
    active = _ordered_unique([
        *(explicit_override or []),
        *state.get("pinned", []),
    ])
    return [name for name in active if name in manual_names and name not in disabled]


def _skill_score(skill, query_norm: str, query_tokens: set[str], telemetry: dict) -> tuple[float, str]:
    activation = getattr(skill, "activation", {}) or {}
    negative_matches = [
        phrase for phrase in activation.get("negative_phrases", [])
        if _phrase_matches(query_norm, phrase)
    ]
    if negative_matches:
        return -10.0, "suppressed by " + ", ".join(negative_matches[:2])

    haystack = _tokens(
        " ".join([
            skill.name,
            skill.display_name,
            skill.description,
            " ".join(skill.tags or []),
        ])
    )
    overlap = query_tokens & haystack
    score = 0.0
    reasons: list[str] = []

    phrase_matches = [
        phrase for phrase in activation.get("phrases", [])
        if _phrase_matches(query_norm, phrase)
    ]
    if phrase_matches:
        score += 10.0 + 3.0 * min(2, len(phrase_matches) - 1)
        reasons.append("phrase " + ", ".join(phrase_matches[:2]))

    keyword_matches: list[str] = []
    for keyword in activation.get("keywords", []):
        keyword_tokens = _tokens(keyword)
        if not keyword_tokens:
            continue
        if len(keyword_tokens) == 1:
            if next(iter(keyword_tokens)) in query_tokens:
                keyword_matches.append(keyword)
        elif keyword_tokens.issubset(query_tokens) or _phrase_matches(query_norm, keyword):
            keyword_matches.append(keyword)
    if keyword_matches:
        score += 3.0 * min(4, len(keyword_matches))
        reasons.append("keyword " + ", ".join(keyword_matches[:3]))

    example_matches: list[str] = []
    for example in activation.get("examples", []):
        if _phrase_matches(query_norm, example):
            example_matches.append(example)
            continue
        example_tokens = _tokens(example)
        if example_tokens:
            ratio = len(query_tokens & example_tokens) / len(example_tokens)
            if ratio >= 0.55 and len(query_tokens & example_tokens) >= 3:
                example_matches.append(example)
    if example_matches:
        score += 7.0
        reasons.append("example match")

    if overlap:
        score += 1.5 * min(3, len(overlap))
        reasons.append("matches " + ", ".join(sorted(overlap)[:3]))
    skill_meta = telemetry.get("skills", {}).get(skill.name, {})
    accepted = int(skill_meta.get("accepted", 0) or 0)
    dismissed = int(skill_meta.get("dismissed", 0) or 0)
    usage_count = int(skill_meta.get("usage_count", 0) or 0)
    score += min(2.0, accepted * 0.35) + min(1.0, usage_count * 0.1)
    score -= min(3.0, dismissed * 0.5)
    if accepted or usage_count:
        reasons.append("used before")
    return score, "; ".join(reasons) if reasons else "related skill"


def suggest_skills(
    thread_id: str,
    current_text: str = "",
    *,
    enabled_tool_names: Iterable[str] | None = None,
    extra_excluded: Iterable[str] | None = None,
    limit: int = 3,
    trace: bool = True,
) -> list[SuggestedSkill]:
    store = _load_store()
    state = _thread_state(store, thread_id)
    if state.get("smart_off"):
        return []
    query_norm = _normalize(current_text)
    query_tokens = _tokens(current_text)
    if not query_tokens:
        return []
    active = set(resolve_active_skill_names(thread_id))
    excluded = (
        active
        | set(state.get("disabled", []))
        | set(state.get("dismissed", []))
        | set(extra_excluded or [])
    )
    ranked: list[SuggestedSkill] = []
    for skill in _available_manual_skills():
        if skill.name in excluded:
            continue
        score, reason = _skill_score(skill, query_norm, query_tokens, store.get("telemetry", {}))
        if score < 4.0:
            continue
        ranked.append(SuggestedSkill(
            name=skill.name,
            display_name=skill.display_name,
            icon=skill.icon,
            description=skill.description,
            reason=reason,
            score=round(score, 2),
        ))
    ranked.sort(key=lambda item: (-item.score, item.display_name.lower()))
    suggestions = ranked[:limit]
    if suggestions and trace:
        record_trace(
            thread_id,
            "suggest",
            suggestions=[s.name for s in suggestions],
            query_tokens=sorted(query_tokens)[:12],
        )
    return suggestions


def get_activation_snapshot(
    thread_id: str,
    *,
    current_text: str = "",
    enabled_tool_names: Iterable[str] | None = None,
    explicit_override: list[str] | None = None,
    is_background: bool = False,
) -> SkillActivationSnapshot:
    store = _load_store()
    state = _thread_state(store, thread_id)
    return SkillActivationSnapshot(
        thread_id=str(thread_id or "default"),
        active=resolve_active_skill_names(
            thread_id,
            explicit_override=explicit_override,
            is_background=is_background,
        ),
        pinned=list(state.get("pinned", [])),
        disabled=list(state.get("disabled", [])),
        dismissed=list(state.get("dismissed", [])),
        smart_off=bool(state.get("smart_off")),
        suggestions=suggest_skills(
            thread_id,
            current_text,
            enabled_tool_names=enabled_tool_names,
            extra_excluded=explicit_override or [],
        ) if not is_background else [],
    )


def _bump_skill_metric(skill_name: str, key: str, *, last_used: bool = False) -> None:
    store = _load_store()
    skills_meta = store.setdefault("telemetry", {}).setdefault("skills", {})
    meta = skills_meta.setdefault(skill_name, {})
    meta[key] = int(meta.get(key, 0) or 0) + 1
    if last_used:
        meta["last_used"] = _now_iso()
    _save_store(store)


def record_usage(thread_id: str, skill_names: Iterable[str], *, source: str = "agent") -> None:
    names = _ordered_unique(skill_names)
    if not names:
        return
    for name in names:
        _bump_skill_metric(name, "usage_count", last_used=True)
    record_trace(thread_id, "activate", skills=names, source=source)


def record_accept(thread_id: str, skill_name: str, *, source: str = "ui") -> None:
    _bump_skill_metric(skill_name, "accepted", last_used=False)
    record_trace(thread_id, "accept", skills=[skill_name], source=source)


def record_dismiss(thread_id: str, skill_name: str, *, source: str = "ui") -> None:
    _bump_skill_metric(skill_name, "dismissed", last_used=False)
    record_trace(thread_id, "dismiss", skills=[skill_name], source=source)


def record_trace(thread_id: str, event: str, **payload) -> None:
    try:
        store = _load_store()
        traces = store.setdefault("telemetry", {}).setdefault("traces", [])
        traces.append({
            "at": _now_iso(),
            "thread_id": str(thread_id or ""),
            "event": event,
            **payload,
        })
        if len(traces) > MAX_TRACES:
            del traces[:-MAX_TRACES]
        _save_store(store)
    except Exception:
        logger.debug("Failed to record Smart Skills trace", exc_info=True)


def get_skill_telemetry() -> dict[str, dict]:
    return dict(_load_store().get("telemetry", {}).get("skills", {}))


def get_thread_activation_state(thread_id: str) -> dict:
    store = _load_store()
    return dict(_thread_state(store, thread_id))
