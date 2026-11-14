"""The person's Proactive choices, in ``proactive.json`` in the data folder.

- **Level**: Off / Quiet / Balanced / Eager. Quiet is the default for new and existing installs.
- **Quiet hours**: nothing reaches the person between them (22:00–07:30 by default).
- **Sources** and **kinds**, each on or off; a source that isn't connected is skipped anyway.
- **Desktop context** for timing (idle, lock, Focus, mic or camera in use, the foreground app's kind): local
  only, its own switch.
- **Urgent kinds**: the only kinds that may reach a chat app (``channel``) when the person is away.

Row-Bot changes these through the self system's settings registry (``self_settings``), with the chat's
approval level deciding who approves; the person changes them in Settings › Proactive.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.proactive.model import KIND_WORDS, KINDS

logger = logging.getLogger(__name__)

_PATH = get_row_bot_data_dir() / "proactive.json"
_LOCK = threading.Lock()

LEVELS: tuple[str, ...] = ("off", "quiet", "balanced", "eager")
LEVEL_WORDS = {"off": "Off", "quiet": "Quiet", "balanced": "Balanced", "eager": "Eager"}
# What each level means, in the person's words (Settings shows it as a live example).
LEVEL_EXAMPLES = {
    "off": "Row-Bot never starts anything by itself.",
    "quiet": "Up to 3 things a day, only at natural breaks; anything urgent (a meeting in 10 minutes) comes first.",
    "balanced": "Up to 6 things a day at natural breaks.",
    "eager": "Up to 12 things a day, as soon as there's a break.",
}
DAILY_CAP = {"off": 0, "quiet": 3, "balanced": 6, "eager": 12}
COOLDOWN_SECONDS = {"off": 0, "quiet": 4 * 3600, "balanced": 2 * 3600, "eager": 30 * 60}

SOURCES: tuple[str, ...] = ("mail", "calendar", "chats", "memory", "workflows", "monitor")
SOURCE_WORDS = {"mail": "Mail", "calendar": "Calendar", "chats": "Your chats", "memory": "Memory",
                "workflows": "Workflows", "monitor": "Monitor and sign-ins"}
# The plain privacy line beside each source: what is read, and what leaves the computer.
_MAIN_MODEL = "a short summary of what changed goes to your main chat model to decide whether it's worth a mention"
PRIVACY = {
    "mail": f"Reads who you wrote to and whether they replied, on this computer; {_MAIN_MODEL}.",
    "calendar": f"Reads your upcoming events and changes to them, on this computer; {_MAIN_MODEL}.",
    "chats": f"Looks for promises and repeated asks in your recent chats, on this computer; {_MAIN_MODEL}.",
    "memory": f"Looks for dates in your memory (birthdays, renewals), on this computer; {_MAIN_MODEL}.",
    "workflows": f"Notices workflows that failed or found something, on this computer; {_MAIN_MODEL}.",
    "monitor": f"Notices sign-ins that stopped working, from Monitor's own checks; {_MAIN_MODEL}.",
    "desktop_context": ("Reads whether you're idle, locked, in Focus or on a call, and the kind of app in front "
                        "(browser, editor, meeting), only to pick a good moment. Never window titles, contents "
                        "or the screen, and none of it leaves this computer."),
}
QUIET_HOURS = ("22:00", "07:30")
DAY_END = "18:00"
FOLLOW_UP_DAYS = 3
# A kind the person ignores this many times in a row goes quiet, with a note in the Activity log.
QUIET_AFTER_IGNORES = 3

DEFAULTS: dict[str, Any] = {
    "level": "quiet",
    "quiet_start": QUIET_HOURS[0],
    "quiet_end": QUIET_HOURS[1],
    "day_end": DAY_END,
    "sources": {source: True for source in SOURCES},
    "kinds": {kind: True for kind in KINDS},
    "desktop_context": True,
    "urgent_kinds": [],
    "channel": "",
    "follow_up_days": FOLLOW_UP_DAYS,
    # The end-of-day wrap: None follows the level (off at Quiet, on at Balanced and Eager); True or False is
    # the person's own choice, whatever the level.
    "wrap": None,
}
WRAP_LEVELS = frozenset({"balanced", "eager"})


def wrap_on(settings: dict[str, Any]) -> bool:
    """Whether the end-of-day wrap comes today."""
    if settings["level"] == "off":
        return False
    return settings["wrap"] if settings["wrap"] is not None else settings["level"] in WRAP_LEVELS
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SettingsError(ValueError):
    """A choice that can't be saved as given, said in words."""


def _clock(value: object, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and text[1] == ":":
        text = "0" + text
    if not _TIME.match(text):
        raise SettingsError(f"{field} is a time like 22:00.")
    return text


def _validated(data: dict[str, Any]) -> dict[str, Any]:
    level = str(data.get("level") or "").lower()
    if level not in LEVELS:
        raise SettingsError("The level is off, quiet, balanced or eager.")
    sources, kinds = data.get("sources") or {}, data.get("kinds") or {}
    if not isinstance(sources, dict) or not isinstance(kinds, dict):
        raise SettingsError("Sources and kinds are switches.")
    if set(sources) - set(SOURCES) or set(kinds) - set(KINDS):
        raise SettingsError("Unknown source or kind.")
    urgent = data.get("urgent_kinds") or []
    if not isinstance(urgent, list) or set(urgent) - set(KINDS):
        raise SettingsError(f"Urgent kinds are some of: {', '.join(KINDS)}.")
    days = data.get("follow_up_days")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
        raise SettingsError("Follow up after 1 to 30 days.")
    start, end = _clock(data.get("quiet_start"), "Quiet hours start"), _clock(data.get("quiet_end"), "Quiet hours end")
    if start == end:
        raise SettingsError("Quiet hours start and end at different times.")
    wrap = data.get("wrap")
    if wrap is not None and not isinstance(wrap, bool):
        raise SettingsError("The end-of-day wrap is on or off.")
    return {
        "wrap": wrap,
        "level": level, "quiet_start": start, "quiet_end": end, "day_end": _clock(data.get("day_end"), "Day end"),
        "sources": {source: bool(sources.get(source, True)) for source in SOURCES},
        "kinds": {kind: bool(kinds.get(kind, True)) for kind in KINDS},
        "desktop_context": bool(data.get("desktop_context")),
        "urgent_kinds": [kind for kind in KINDS if kind in urgent],
        "channel": str(data.get("channel") or "").strip()[:64],
        "follow_up_days": days,
    }


def load() -> dict[str, Any]:
    """The saved choices, defaults filled in; the defaults when nothing was saved or the file is unreadable."""
    try:
        saved = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    except (OSError, ValueError):
        logger.warning("Proactive settings could not be read; using the defaults", exc_info=True)
        saved = {}
    merged = {**DEFAULTS, **(saved if isinstance(saved, dict) else {})}
    merged["sources"] = {**DEFAULTS["sources"], **(merged.get("sources") or {})}
    merged["kinds"] = {**DEFAULTS["kinds"], **(merged.get("kinds") or {})}
    try:
        return _validated(merged)
    except SettingsError:
        logger.warning("Proactive settings were invalid; using the defaults")
        return _validated(dict(DEFAULTS))


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """Merge *changes* into the saved choices, check them and save; the new choices."""
    with _LOCK:
        current = load()
        merged = {**current, **changes}
        for key in ("sources", "kinds"):
            if key in changes:
                merged[key] = {**current[key], **(changes[key] or {})}
        result = _validated(merged)
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    turned_on = [kind for kind, on in result["kinds"].items() if on and not current["kinds"][kind]]
    if turned_on:
        from row_bot.proactive import store

        for kind in turned_on:  # Turning a kind back on is the person saying they want it: it speaks again.
            store.set_quieted(kind, 0.0)
    wanted = (["brief"] if "brief" in turned_on else []) + (["wrap"] if result["wrap"] and not current["wrap"] else [])
    if wanted:
        from row_bot.proactive import day

        for which in wanted:  # Its workflow was deleted: asking for it again brings it back.
            day.forget_deleted(which)
    return result


# --- Row-Bot's changes, through the self system's settings registry ----------------------------------------

def _value(name: str, settings: dict[str, Any]) -> tuple[str, str]:
    if name == "level":
        return settings["level"], LEVEL_WORDS[settings["level"]]
    if name == "quiet_hours":
        span = f"{settings['quiet_start']}-{settings['quiet_end']}"
        return span, span.replace("-", "–")
    if name == "desktop_context":
        return ("on", "On") if settings["desktop_context"] else ("off", "Off")
    raise KeyError(name)


def current_value(name: str):
    """The registry's view of one setting now."""
    from row_bot.self_settings import Value

    return Value(*_value(name, load()))


def _toggle(value: str, names: tuple[str, ...], what: str) -> tuple[str, bool]:
    from row_bot.self_settings import SettingRefused, _on_off

    name, sep, switch = value.rpartition(":")
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    if not sep or name not in names:
        raise SettingRefused(f"Use '{what}:on' or '{what}:off' with one of: {', '.join(names)}.")
    return name, _on_off(switch)


def prepare_change(name: str, setting, value: str):
    """A checked change for one of Proactive's registered settings (``self_settings``)."""
    from row_bot.self_settings import Change, SettingRefused, Value, _change, _on_off

    settings = load()
    text = (value or "").strip()
    if name == "level":
        level = text.lower()
        if level not in LEVELS:
            raise SettingRefused("Use off, quiet, balanced or eager.")
        changes, after = {"level": level}, Value(level, LEVEL_WORDS[level])
    elif name == "quiet_hours":
        match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*", text)
        if not match:
            raise SettingRefused("Use 'START-END' times, like '22:00-07:30'.")
        changes = {"quiet_start": match.group(1).zfill(5), "quiet_end": match.group(2).zfill(5)}
        span = f"{changes['quiet_start']}-{changes['quiet_end']}"
        after = Value(span, span.replace("-", "–"))
    elif name == "desktop_context":
        on = _on_off(text)
        changes, after = {"desktop_context": on}, Value("on" if on else "off", "On" if on else "Off")
    else:
        names, words, field = {"kind": (KINDS, KIND_WORDS, "kinds"), "source": (SOURCES, SOURCE_WORDS, "sources"),
                               "urgent": (KINDS, KIND_WORDS, "urgent_kinds")}[name]
        item, on = _toggle(text, names, name)
        if field == "urgent_kinds":
            was = item in settings["urgent_kinds"]
            kept = [kind for kind in settings["urgent_kinds"] if kind != item] + ([item] if on else [])
            changes = {"urgent_kinds": kept}
            states = ("Urgent", "Not urgent")
        else:
            was = settings[field][item]
            changes = {field: {item: on}}
            states = ("On", "Off")
        label = f"{setting.label}: {words[item]}"

        def apply_toggle() -> str:
            save(changes)
            return f"{label} is {(states[0] if on else states[1]).lower()}."

        before = Value(f"{item}:{'on' if was else 'off'}", states[0] if was else states[1])
        after_toggle = Value(f"{item}:{'on' if on else 'off'}", states[0] if on else states[1])
        return Change(setting.id, label, setting.where, setting.href, before, after_toggle, setting.tier,
                      f"Turn {'on' if on else 'off'} {label}" if field != "urgent_kinds"
                      else f"{'Mark' if on else 'Unmark'} {words[item]} as urgent",
                      f"{label}: {before.words} → {after_toggle.words}", apply_toggle,
                      f"{label} wasn't changed.")
    try:
        _validated({**settings, **changes})
    except SettingsError as exc:
        raise SettingRefused(str(exc)) from None

    def apply() -> str:
        save(changes)
        return f"{setting.label} set to {after.words}."

    return _change(setting, Value(*_value(name, settings)), after, apply)
